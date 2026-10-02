"""Read-only activity viewer; no interactive Python execution or stack locals."""
from collections import OrderedDict
from datetime import datetime, timezone
import json
from threading import Lock
from time import monotonic

from flask import Blueprint, Response, current_app, jsonify, render_template

bp = Blueprint('debugger', __name__)


class Activity:
    def __init__(self, logger, redact, limit=100, event_limit=200):
        self.logger, self.redact = logger, redact
        self.limit, self.event_limit = limit, event_limit
        self.runs = OrderedDict()
        self.lock = Lock()

    def start(self, reference, route):
        with self.lock:
            self.runs[reference] = dict(id=reference, route=route, status='running',
                started=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                clock=monotonic(), events=[], dropped=0)
            while len(self.runs) > self.limit:
                # Prefer discarding finished runs while work is in progress.
                victim = next((key for key, run in self.runs.items() if run['status'] != 'running'), next(iter(self.runs)))
                del self.runs[victim]
        self.add(reference, 'Request received', {'state': 'started', 'route': route})

    def add(self, reference, label, details):
        safe = {'label': self.redact(str(label)), 'details': {
            key: value if isinstance(value, (int, float, bool)) else self.redact(str(value))[:600]
            for key, value in details.items()}}
        with self.lock:
            run = self.runs.get(reference)
            if run is None:
                return
            row = dict(time=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       elapsed=round(monotonic() - run['clock'], 2), **safe)
            run['events'].append(row)
            if len(run['events']) > self.event_limit:
                run['events'].pop(0)
                run['dropped'] += 1
        self.logger.info('activity request_id=%s %s', reference, json.dumps(row, ensure_ascii=True))

    def finish(self, reference, status, http_status):
        with self.lock:
            run = self.runs.get(reference)
            if run is None or run['status'] != 'running':
                return
            run.update(status=status, seconds=round(monotonic() - run['clock'], 2))
        self.add(reference, 'Request finished', {'state': status, 'http_status': http_status})

    def snapshot(self):
        with self.lock:
            return [dict(id=r['id'], route=r['route'], status=r['status'], started=r['started'],
                         seconds=r.get('seconds', round(monotonic()-r['clock'], 2)),
                         events=list(r['events']), dropped=r['dropped'])
                    for r in reversed(self.runs.values())]


@bp.get('/debug')
def page():
    response = current_app.make_response(render_template('debug.html'))
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/debug/data')
def data():
    response = jsonify(runs=current_app.extensions['activity'].snapshot())
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/debug/download')
def download():
    # Export only the bounded, redacted activity structure. Full error traces
    # remain in the local rotating log, linked by the same request reference.
    report = json.dumps({'runs': current_app.extensions['activity'].snapshot()}, indent=2)
    return Response(report, mimetype='application/json', headers={
        'Content-Disposition': 'attachment; filename="reelfactory-debug.json"', 'Cache-Control': 'no-store'})
