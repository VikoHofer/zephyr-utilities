"""Board revision predictions from the exact environment captured by a profile."""

from ...core.board_revisions import resolve_board_revision
from .records import identifier


def _unavailable(profile, reason):
    target = profile.get("target") if isinstance(profile, dict) else None
    requested = target.split("/", 1)[0].partition("@")[2] or None if isinstance(target, str) else None
    return {"requested": requested, "effective": None, "format": None, "supported": False, "reason": reason}


def captured_revision(board, requested):
    """Resolve declarations without replacing the profile's requested target."""
    if not isinstance(board, dict):
        return {"requested": requested, "effective": None, "format": None, "supported": False,
                "reason": "The selected board's captured revision evidence is unavailable."}
    if board.get("revision_format") is None:
        return None
    try:
        revisions = board.get("revisions")
        if not isinstance(revisions, list):
            raise ValueError("Captured board revision names must be a list.")
        metadata = {"format": board["revision_format"], "default": board.get("default_revision"),
                    "exact": board.get("revision_exact", False),
                    "revisions": [{"name": name} for name in revisions]}
        return {**resolve_board_revision(metadata, requested), "supported": True}
    except (ValueError, TypeError) as exc:
        return {"requested": requested, "effective": None, "format": board.get("revision_format"),
                "supported": False, "reason": str(exc)}


def snapshot_revision(profile, snapshot):
    """Use only matching captured evidence, never another workspace or a newer default."""
    if (not isinstance(snapshot, dict) or not isinstance(profile, dict)
            or snapshot.get("id") != profile.get("environment_id")
            or snapshot.get("fingerprint") != profile.get("environment_fingerprint")
            or not isinstance(snapshot.get("workspace"), dict)
            or snapshot["workspace"].get("path") != profile.get("workspace")):
        return _unavailable(profile, "The captured environment observation does not match this profile.")
    boards, target = snapshot.get("boards"), profile.get("target")
    if not isinstance(boards, list) or not isinstance(target, str):
        return _unavailable(profile, "The captured environment has no usable board selection evidence.")
    board = next((item for item in boards if isinstance(item, dict) and item.get("id") == profile.get("board_id")), None)
    if board is None or not isinstance(board.get("targets"), list) or target not in board["targets"]:
        return _unavailable(profile, "The captured environment has no board matching this profile's target.")
    requested = target.split("/", 1)[0].partition("@")[2] or None
    return captured_revision(board, requested)


def saved_revision(store, profile):
    """Look up one profile's saved observation; missing evidence remains unavailable."""
    try:
        observation_id = identifier(profile.get("environment_id"))
        with store.lock:
            snapshot = store._read(store.root / "snapshots" / f"{observation_id}.json")
        return snapshot_revision(profile, snapshot)
    except (OSError, ValueError, TypeError, AttributeError):
        return _unavailable(profile, "The saved environment observation is unavailable; automatic board revision selection is unknown.")
