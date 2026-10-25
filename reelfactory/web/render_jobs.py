"""Render history and portable presets for the local editor."""
import json

from flask import Blueprint, current_app, redirect, render_template, request, Response, url_for

from .. import cli, effects, templates
from ..render_jobs import OPTIONAL_SETTINGS, RenderQueue, SETTINGS
from ..script import LANGS

bp = Blueprint("render_jobs", __name__, url_prefix="/renders")


def queue():
    return current_app.extensions["render_queue"]


def register(app, workspace, out_root):
    app.extensions["render_queue"] = RenderQueue(workspace, out_root, app.extensions["error_logger"])
    app.register_blueprint(bp)


@bp.get("")
def page():
    return render_template("render_jobs.html", jobs=queue().jobs(), error=None)


@bp.get("/status")
def status():
    return {"jobs": queue().jobs()}


@bp.post("/start")
def start():
    queue().start()
    return redirect(url_for("render_jobs.page"))


@bp.post("/<id>/<action>")
def action(id, action):
    try:
        if action in {"cancel", "stop"}:
            queue().cancel(id)
        elif action == "retry":
            queue().retry(id)
            queue().start()
        elif action == "repeat":
            queue().repeat(id)
            queue().start()
        else:
            return "No such action.", 404
    except ValueError as exc:
        return render_template("render_jobs.html", jobs=queue().jobs(), error=str(exc)), 400
    return redirect(url_for("render_jobs.page"))


def download(data, filename):
    return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.get("/<id>/recipe")
def recipe(id):
    try:
        job = queue().store.get(id, "render")
        return download(queue().recipe(job), f"render-{id}.json")
    except (ValueError, OSError):
        return {"error": "This recipe no longer exists."}, 404


def validate_preset(data):
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Choose a Reel Factory preset with schema 1.")
    name, values = data.get("name"), data.get("settings")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("Give the preset a name of 1 to 80 characters.")
    allowed = set(SETTINGS) | {"lang", "aspect"}
    required = allowed - set(OPTIONAL_SETTINGS)
    if not isinstance(values, dict) or not required <= set(values) <= allowed:
        raise ValueError("This preset has missing or unsupported settings.")
    selection = values.get("visual_effects", "none")
    if not isinstance(selection, str) or selection not in effects.PRESETS:
        raise ValueError("Choose a valid visual effects option in the preset.")
    if "effect_strength" in values:
        if type(values["effect_strength"]) not in (int, float):
            raise ValueError("Choose a valid visual effect intensity in the preset.")
        effects.parse_strength(values["effect_strength"])
    for key, choices in (("tts", cli.TTS_CHOICES), ("preset", cli.PRESETS),
                         ("template", [""] + templates.available()),
                         ("voice_rate", ["", "-10%", "+0%", "+6%"])):
        if not isinstance(values[key], str) or values[key] not in choices:
            raise ValueError(f"Choose a supported {key} in the preset.")
    if (not isinstance(values["voice_delivery"], str) or len(values["voice_delivery"]) > 1500
            or type(values["no_music"]) is not bool):
        raise ValueError("Invalid voice or music settings in this preset.")
    for key, choices in (("lang", LANGS), ("aspect", cli.ASPECTS)):
        if (not isinstance(values[key], list) or not values[key] or len(values[key]) > len(choices)
                or any(not isinstance(v, str) or v not in choices for v in values[key])):
            raise ValueError(f"Choose valid {key} values in the preset.")
    return {"schema": 1, "name": name.strip(), "settings": values}


@bp.get("/presets")
def presets():
    return {"presets": queue().store.list("preset")}


@bp.post("/presets")
def save_preset():
    if request.content_length and request.content_length > 32768:
        return {"error": "Preset files must be smaller than 32 KB."}, 413
    try:
        data = validate_preset(request.get_json(silent=True))
        if len(queue().store.list("preset")) >= 100:
            raise ValueError("You can save up to 100 presets. Delete an old preset first.")
        return queue().store.add("preset", data), 201
    except ValueError as exc:
        return {"error": str(exc)}, 400


@bp.get("/presets/<id>")
def export_preset(id):
    try:
        data = queue().store.get(id, "preset")
        return download({k: data[k] for k in ("schema", "name", "settings")}, "reel-factory-preset.json")
    except ValueError as exc:
        return {"error": str(exc)}, 404


@bp.post("/presets/<id>/delete")
def delete_preset(id):
    with queue().store.connection() as db:
        db.execute("DELETE FROM records WHERE id=? AND kind='preset'", (id,))
    return {"deleted": True}
