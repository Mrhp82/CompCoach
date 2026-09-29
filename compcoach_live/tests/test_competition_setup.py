from io import BytesIO

from PIL import Image

from compcoach_live.asset_store import LocalAssetStore
from compcoach_live.competition_setup import (
    _date_range_label,
    _save_profile_metadata,
    _save_uploaded_asset,
)


class FakeCompetitionDB:
    def __init__(self):
        self.competition = {
            "id": "october_nac",
            "name": "Original competition",
            "location": "Original hall",
            "start_date": "2026-10-09",
            "end_date": "2026-10-12",
            "timezone": "America/Denver",
            "logo_path": "competitions/october_nac/logo.png",
            "strip_map_path": "competitions/october_nac/strip-maps/old.png",
        }
        self.updates = []

    def get_competition(self, competition_id):
        if competition_id != self.competition["id"]:
            return None
        return dict(self.competition)

    def update_competition(self, competition_id, **values):
        assert competition_id == self.competition["id"]
        self.updates.append(dict(values))
        self.competition.update(
            {
                **values,
                "timezone": values["timezone_name"],
            }
        )
        return dict(self.competition)


def _png_bytes():
    output = BytesIO()
    Image.new("RGB", (32, 20), "navy").save(output, format="PNG")
    return output.getvalue()


def test_metadata_save_preserves_both_asset_paths():
    db = FakeCompetitionDB()

    _save_profile_metadata(
        db,
        "october_nac",
        name="October NAC",
        location="Salt Palace",
        start_date="2026-10-09",
        end_date="2026-10-12",
        timezone_name="America/Denver",
    )

    update = db.updates[-1]
    assert update["logo_path"] == "competitions/october_nac/logo.png"
    assert update["strip_map_path"] == (
        "competitions/october_nac/strip-maps/old.png"
    )


def test_logo_save_preserves_current_metadata_and_strip_map(tmp_path):
    db = FakeCompetitionDB()

    _save_uploaded_asset(
        db,
        LocalAssetStore(tmp_path),
        "october_nac",
        kind="logo",
        payload=_png_bytes(),
        filename="logo.png",
        content_type="image/png",
    )

    update = db.updates[-1]
    assert update["name"] == "Original competition"
    assert update["location"] == "Original hall"
    assert update["start_date"] == "2026-10-09"
    assert update["end_date"] == "2026-10-12"
    assert update["timezone_name"] == "America/Denver"
    assert update["logo_path"] == "competitions/october_nac/logo.png"
    assert update["strip_map_path"] == (
        "competitions/october_nac/strip-maps/old.png"
    )


def test_strip_map_save_preserves_current_metadata_and_logo(tmp_path):
    db = FakeCompetitionDB()

    _save_uploaded_asset(
        db,
        LocalAssetStore(tmp_path),
        "october_nac",
        kind="strip_map",
        payload=_png_bytes(),
        filename="hall.png",
        content_type="image/png",
    )

    update = db.updates[-1]
    assert update["name"] == "Original competition"
    assert update["logo_path"] == "competitions/october_nac/logo.png"
    assert update["strip_map_path"] == (
        "competitions/october_nac/strip-maps/main.png"
    )


def test_date_range_label_is_compact_and_accepts_partial_dates():
    assert _date_range_label("2026-10-09", "2026-10-12") == (
        "Oct 9, 2026 – Oct 12, 2026"
    )
    assert _date_range_label("2026-10-09", "") == "Oct 9, 2026"
    assert _date_range_label("", "") == ""
