"""Integration tests against a real Postgres.

Concentrated on the three things most likely to be wrong and most expensive if they are:
the publish job's atomicity and idempotency, role enforcement, and whether the seed's
planted defects are actually caught rather than silently swallowed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Episode, PublishStatus, Show, Status
from app.services import publish_service
from app.services.validation import collect_issues


# ------------------------------------------------------------------ seed ingest
class TestSeedIngest:
    def test_loads_all_95_episodes(self, seeded):
        db = seeded["db"]
        assert db.scalar(select(Show).where(Show.slug == "motis-many-lives")) is not None
        assert len(db.scalars(select(Episode)).all()) == 95, (
            "every seed row must load, including the broken ones — dropping rows would "
            "hide the problems the report exists to surface"
        )
        assert len(db.scalars(select(Show)).all()) == 8

    def test_planted_defects_are_recorded_not_repaired(self, seeded):
        db = seeded["db"]
        findings = " | ".join(seeded["ingest"]["findings"])

        # The duplicate (content_group, language) row.
        dup = db.scalar(select(Episode).where(Episode.external_id == "ep_9001"))
        assert dup is not None, "the colliding row is kept as evidence"
        assert dup.status is Status.draft, "and quarantined rather than published"
        assert dup.seed_issue and "duplicate content_group" in dup.seed_issue

        # The show with no section.
        rr = db.scalar(select(Show).where(Show.slug == "rhyme-rangers"))
        assert rr.section is None
        assert rr.status is Status.draft

        # The published-but-artwork-less episode.
        ep36 = db.scalar(select(Episode).where(Episode.external_id == "ep_0036"))
        assert ep36.status is Status.draft
        assert "artwork" in (ep36.seed_issue or "")

        assert "ep_9001" in findings and "ep_0036" in findings

    def test_content_group_uniqueness_holds_in_the_database(self, seeded):
        db = seeded["db"]
        pairs = [
            (e.content_group, e.language)
            for e in db.scalars(select(Episode)).all()
            if e.content_group
        ]
        assert len(pairs) == len(set(pairs)), "the partial unique index must hold"

    def test_ingest_is_idempotent(self, seeded):
        from app.services import seed_ingest

        again = seed_ingest.seed_content(
            seeded["db"], seeded["reference"], seeded["storage"], seeded["seed_dir"]
        )
        assert again["skipped"] is True
        assert len(seeded["db"].scalars(select(Episode)).all()) == 95


# ---------------------------------------------------------------- validation
class TestValidationReport:
    def test_report_groups_by_show_and_names_the_fix(self, seeded):
        report = collect_issues(seeded["db"], seeded["reference"])
        assert isinstance(report["blocking_publish"], bool)
        for show in report["shows"]:
            assert show["title"]
            for issue in show["issues"]:
                assert issue["message"].strip()
                assert issue["entity"]["type"] in {"show", "episode"}
                assert issue["severity"] in {"blocker", "warning"}

    def test_duplicate_show_warning_is_raised_not_acted_on(self, seeded):
        """peblo-songs vs peblo-songs-lyrical: flagged for a human, never merged."""
        report = collect_issues(seeded["db"], seeded["reference"])
        codes = [i["code"] for i in report["global_issues"]]
        assert "possible_duplicate_shows" in codes
        assert all(
            i["severity"] == "warning"
            for i in report["global_issues"]
            if i["code"] == "possible_duplicate_shows"
        )
        # Both shows still exist independently.
        db = seeded["db"]
        assert db.scalar(select(Show).where(Show.slug == "peblo-songs")) is not None
        assert db.scalar(select(Show).where(Show.slug == "peblo-songs-lyrical")) is not None


# ------------------------------------------------------------------- publishing
class TestPublish:
    def test_publish_records_a_run_and_flips_the_pointer(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        result = publish_service.publish(db, ref, storage, actor_id=None)
        assert result["status"] == "succeeded"

        run = publish_service.current_run(db)
        assert run is not None
        assert run.status is PublishStatus.succeeded
        assert run.is_current is True
        assert run.catalog_key == f"catalogs/{run.id}.json"
        assert storage.exists(run.catalog_key)
        assert run.counts["episodes"] > 0

    def test_republishing_unchanged_content_is_a_noop(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        first = publish_service.publish(db, ref, storage, actor_id=None)
        second = publish_service.publish(db, ref, storage, actor_id=None)

        assert second["status"] == "noop"
        assert second["checksum"] == first["checksum"]
        # The pointer still refers to the first run; a noop writes no new file.
        assert publish_service.current_run(db).id != second["run_id"]
        assert not storage.exists(f"catalogs/{second['run_id']}.json")

    def test_a_change_produces_a_new_catalogue(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        first = publish_service.publish(db, ref, storage, actor_id=None)

        show = db.scalar(select(Show).where(Show.status == Status.published))
        show.title = show.title + " (updated)"
        db.commit()

        second = publish_service.publish(db, ref, storage, actor_id=None)
        assert second["status"] == "succeeded"
        assert second["checksum"] != first["checksum"]
        assert publish_service.current_run(db).id == second["run_id"]
        # The previous catalogue file is still there, untouched — publishing never
        # overwrites the live object.
        assert storage.exists(f"catalogs/{first['run_id']}.json")

    def test_crash_during_write_leaves_the_old_catalogue_serving(self, seeded, monkeypatch):
        """The atomicity claim, tested rather than asserted."""
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        good = publish_service.publish(db, ref, storage, actor_id=None)
        before = storage.get(f"catalogs/{good['run_id']}.json")

        show = db.scalar(select(Show).where(Show.status == Status.published))
        show.title = show.title + " (doomed)"
        db.commit()

        def explode(*args, **kwargs):
            raise RuntimeError("storage is down")

        monkeypatch.setattr(storage, "put", explode)
        with pytest.raises(RuntimeError):
            publish_service.publish(db, ref, storage, actor_id=None)
        monkeypatch.undo()

        # The pointer never moved, and the bytes readers see are unchanged.
        current = publish_service.current_run(db)
        assert current.id == good["run_id"]
        assert storage.get(current.catalog_key) == before

        # The failed attempt is recorded, not silent.
        failed = db.scalars(
            select(publish_service.PublishRun).where(
                publish_service.PublishRun.status == PublishStatus.failed
            )
        ).all()
        assert len(failed) == 1
        assert "storage is down" in failed[0].error

    def test_only_published_content_reaches_the_catalogue(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        publish_service.publish(db, ref, storage, actor_id=None)
        catalog, _ = publish_service.load_current_catalog(db, storage)

        titles = {s["title"] for sec in catalog["sections"] for s in sec["shows"]}
        assert "Rhyme Rangers" not in titles, "sectionless draft show must not appear"

        # ep_9001 was quarantined; its title must not appear anywhere.
        all_entry_titles = {
            e["title"]
            for sec in catalog["sections"]
            for s in sec["shows"]
            for season in s["seasons"]
            for e in season["entries"]
        }
        assert "The Lost Kite (v2)" not in all_entry_titles

    def test_language_variants_collapse_in_the_real_catalogue(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        publish_service.publish(db, ref, storage, actor_id=None)
        catalog, _ = publish_service.load_current_catalog(db, storage)

        multi = [
            e
            for sec in catalog["sections"]
            for s in sec["shows"]
            for season in s["seasons"]
            for e in season["entries"]
            if len(e["languages"]) > 1
        ]
        assert multi, "the seed contains en/hi pairs that must collapse"
        assert all(e["languages"] == sorted(e["languages"]) for e in multi)
        assert catalog["counts"]["entries"] < catalog["counts"]["episodes"]

    def test_season_zero_becomes_trailers_in_the_real_catalogue(self, seeded):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        publish_service.publish(db, ref, storage, actor_id=None)
        catalog, _ = publish_service.load_current_catalog(db, storage)

        shows = [s for sec in catalog["sections"] for s in sec["shows"]]
        with_trailers = [s for s in shows if s["trailers"]]
        assert with_trailers, "the seed has two Season 0 trailers"
        for s in shows:
            assert all(season["number"] != 0 for season in s["seasons"])


# ------------------------------------------------------------------------- auth
class TestRolesAreEnforced:
    def _token(self, client, email, password):
        res = client.post("/api/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        return res.json()["access_token"]

    def test_editor_cannot_publish(self, seeded, client):
        """The single most important auth test: roles enforced, not just declared."""
        token = self._token(client, "editor@peblo.test", "editor123")
        res = client.post(
            "/api/admin/catalog/publish", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 403
        body = res.json()
        assert body["error"]["code"] == "forbidden"
        assert "admin" in body["error"]["message"].lower()

    def test_anonymous_cannot_read_admin_or_crud(self, seeded, client):
        for method, path in [
            ("get", "/api/shows"),
            ("get", "/api/admin/validation-report"),
            ("post", "/api/admin/catalog/publish"),
        ]:
            res = getattr(client, method)(path)
            assert res.status_code == 401, f"{path} must require sign-in"
            assert res.json()["error"]["code"] == "unauthorized"

    def test_viewer_endpoints_are_public(self, seeded, client):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        publish_service.publish(db, ref, storage, actor_id=None)

        assert client.get("/api/catalog").status_code == 200
        assert client.get("/api/catalog/search?q=moti").status_code == 200

    def test_admin_can_publish(self, seeded, client):
        token = self._token(client, "admin@peblo.test", "admin123")
        res = client.post(
            "/api/admin/catalog/publish", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code in (200, 422)
        if res.status_code == 422:
            # Blocked by content, not by permissions — that is a different failure.
            assert res.json()["error"]["code"] == "publish_blocked"


# ----------------------------------------------------------------------- catalog
class TestCatalogEndpoint:
    def test_etag_returns_304_on_revalidation(self, seeded, client):
        db, ref, storage = seeded["db"], seeded["reference"], seeded["storage"]
        publish_service.publish(db, ref, storage, actor_id=None)

        first = client.get("/api/catalog")
        assert first.status_code == 200
        etag = first.headers["ETag"]

        second = client.get("/api/catalog", headers={"If-None-Match": etag})
        assert second.status_code == 304

    def test_no_catalog_yet_is_a_friendly_404(self, app_env, client):
        res = client.get("/api/catalog")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "no_catalog_published"


class TestSearch:
    def _publish(self, seeded):
        publish_service.publish(seeded["db"], seeded["reference"], seeded["storage"], actor_id=None)

    def test_q_matches_show_title_and_episode_title_and_category(self, seeded, client):
        self._publish(seeded)
        assert client.get("/api/catalog/search?q=moti").json()["total"] > 0
        assert client.get("/api/catalog/search?q=kite").json()["total"] > 0
        assert client.get("/api/catalog/search?q=adventure").json()["total"] > 0

    def test_filters_compose_with_and(self, seeded, client):
        self._publish(seeded)
        broad = client.get("/api/catalog/search?q=the").json()["total"]
        narrow = client.get("/api/catalog/search?q=the&language=hi").json()["total"]
        assert narrow <= broad

        both = client.get("/api/catalog/search?section=featured&language=hi").json()
        for r in both["results"]:
            assert r["section"] == "featured"
            assert "hi" in r["entry"]["languages"]

    def test_empty_result_has_a_usable_shape(self, seeded, client):
        self._publish(seeded)
        body = client.get("/api/catalog/search?q=zzzznotathing").json()
        assert body["total"] == 0
        assert body["results"] == []
        assert body["facets"]["categories"], "facets still populated so filters render"


class TestHealth:
    def test_health_checks_dependencies(self, seeded, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["storage"] == "ok"
