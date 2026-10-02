import logging
from pathlib import Path
import threading

import pytest
from flask import render_template_string

from reelfactory import telemetry
from reelfactory.web.debugger import Activity
from reelfactory.web.diagnostics import record_failure


def test_successful_operation_has_stages_and_matching_request_id(client, app):
    @app.post('/test/stages')
    def operation():
        with telemetry.stage('Example stage'):
            telemetry.event('Counts', photos=3)
        return 'ok'
    response = client.post('/test/stages', data={'secret': 'do-not-log-body'})
    data = client.get('/debug/data')
    run = data.json['runs'][0]
    assert run['id'] == response.headers['X-Request-ID']
    assert run['status'] == 'completed'
    assert [e['details']['state'] for e in run['events'] if e['label'] == 'Example stage'] == ['started', 'completed']
    assert 'do-not-log-body' not in data.get_data(as_text=True)
    assert data.headers['Cache-Control'] == 'no-store'
    assert len(client.get('/debug/data').json['runs']) == 1  # polling is not traced
    assert telemetry.sink.get() is None


def test_handled_error_with_http_200_is_failed_and_redacted(client, app, monkeypatch, project):
    monkeypatch.setenv('GEMINI_API_KEY', 'secret-primary-value')
    monkeypatch.setenv('ELEVENLABS_API_KEYS', 'secret-list-one,secret-list-two')
    @app.post('/test/error')
    def operation():
        error = ValueError('Rejected secret-primary-value secret-list-one secret-list-two Bearer secret-token-value')
        record_failure(error)
        return render_template_string('Retry', error=str(error))
    assert client.post('/test/error').status_code == 200
    response = client.get('/debug/download')
    run = response.json['runs'][0]
    assert run['status'] == 'failed'
    text = response.get_data(as_text=True)
    for secret in ('secret-primary-value', 'secret-list-one', 'secret-list-two', 'secret-token-value'):
        assert secret not in text
        assert secret not in Path(app.config['ERROR_LOG_PATH']).read_text(encoding='utf-8')
    assert '[REDACTED]' in text
    assert 'attachment;' in response.headers['Content-Disposition']


def test_activity_can_be_polled_while_work_is_running(app):
    entered, release = threading.Event(), threading.Event()
    results = []
    @app.post('/test/slow')
    def operation():
        with telemetry.stage('Waiting on service'):
            entered.set()
            assert release.wait(5)
        return 'ok'
    def submit():
        with app.test_client() as client:
            results.append(client.post('/test/slow').status_code)
    worker = threading.Thread(target=submit)
    worker.start()
    try:
        assert entered.wait(5)
        with app.test_client() as client:
            run = client.get('/debug/data').json['runs'][0]
            assert run['status'] == 'running'
            assert run['events'][-1]['label'] == 'Waiting on service'
            assert run['events'][-1]['details']['state'] == 'started'
            assert telemetry.sink.get() is None
    finally:
        release.set()
        worker.join(5)
    assert results == [200]
    with app.test_client() as client:
        assert client.get('/debug/data').json['runs'][0]['status'] == 'completed'


def test_history_and_events_are_bounded():
    activity = Activity(logging.getLogger('test.activity'), lambda text: text, limit=2, event_limit=3)
    for reference in ('one', 'two', 'three'):
        activity.start(reference, '/test')
        for i in range(8):
            activity.add(reference, 'Step', {'number': i})
        activity.finish(reference, 'completed', 200)
    runs = activity.snapshot()
    assert [r['id'] for r in runs] == ['three', 'two']
    assert all(len(r['events']) == 3 and r['dropped'] == 7 for r in runs)


def test_instrumentation_failure_cannot_break_work():
    def broken(label, details):
        raise OSError('full disk')
    token = telemetry.sink.set(broken)
    try:
        with telemetry.stage('Test'):
            telemetry.event('Still working')
    finally:
        telemetry.sink.reset(token)


def test_real_script_request_is_instrumented(client):
    client.post('/products/test-rack/script', data={'lang': 'en', 'script': 'template'})
    run = client.get('/debug/data').json['runs'][0]
    assert run['status'] == 'completed'
    assert any(e['label'] == 'Script configuration' and e['details']['writer'] == 'template' for e in run['events'])
    assert any(e['label'] == 'Write script' and e['details']['state'] == 'completed' for e in run['events'])
