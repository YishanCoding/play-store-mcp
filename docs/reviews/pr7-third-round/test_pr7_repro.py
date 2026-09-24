"""Read-only, credential-free characterization probes for PR #7 at 0777ec6.

Native checkout, no repository changes:
  PYTHONPATH="$PWD/src" python -m pytest -q -s -p no:cacheprovider \
    /absolute/path/to/test_pr7_repro.py

Assertions intentionally characterize the current defect AND passing controls.
A passing reproduction is evidence of a defect, not a passing product contract.
All client/network boundaries are mocked; --yes never reaches a live service.
"""
from __future__ import annotations
import io
import json
import socket
from typing import Any
from unittest.mock import MagicMock, call
import pytest
from googleapiclient.errors import HttpError
import play_store_mcp.cli as cli
import play_store_mcp.tools as tools
from play_store_mcp.cli.client import CliPlayStoreClient
from play_store_mcp.models import ReviewReplyResult

PKG = 'com.example.app'

@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    previous = tools._client_provider
    monkeypatch.delenv('GPCLI_PACKAGE', raising=False)
    monkeypatch.delenv('GOOGLE_PLAY_STORE_CREDENTIALS', raising=False)
    monkeypatch.setattr(cli, '_configure_logging', lambda **kwargs: None)
    def denied(*args, **kwargs):
        raise AssertionError('A real network connection was attempted')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    yield
    tools._client_provider = previous


def run(argv, client):
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(argv, client=client, stdout=out, stderr=err)
    return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()


def evidence(name, **data):
    print(json.dumps({'probe': name, **data}, ensure_ascii=False, default=str))


def model(data):
    obj = MagicMock()
    obj.model_dump.return_value = data
    return obj


def http(status):
    resp = MagicMock()
    resp.status = status
    resp.reason = 'error'
    resp.get.return_value = '0'
    return HttpError(resp, json.dumps({'error': {'code': status, 'message': 'denied'}}).encode(), uri='https://mock.invalid/')


def client_with_service():
    c = object.__new__(CliPlayStoreClient)
    c._logger = MagicMock()
    s = MagicMock()
    trace = []
    c._get_service = MagicMock(return_value=s)
    c._create_edit = MagicMock(side_effect=lambda p: trace.append(('create', p)) or 'edit-1')
    c._commit_edit = MagicMock(side_effect=lambda p, e: trace.append(('commit', p, e)))
    c._delete_edit = MagicMock(side_effect=lambda p, e: trace.append(('delete', p, e)))
    c.validate_listing_text = MagicMock(return_value=[])
    return c, s, trace


def raw(id, valid=True):
    return {'reviewId': id, 'comments': [{'userComment': {'text': 'hello', 'starRating': 5, 'reviewerLanguage': 'en'}}] if valid else []}


def review_api(pages):
    c, s, trace = client_with_service()
    it = iter(pages)
    def execute():
        page = next(it)
        if isinstance(page, BaseException):
            raise page
        return page
    s.reviews.return_value.list.return_value.execute.side_effect = execute
    return c, s.reviews.return_value.list


def test_F01_query_rollout_default_overrides_one_percent():
    observed = {}
    for style, option in (
        ('query', ['--query', '{"rollout_percentage":1}']),
        ('body', ['--body', '{"rollout_percentage":1}']),
        ('flag', ['--rollout-percentage', '1']),
    ):
        c, s, trace = client_with_service()
        tracks = s.edits.return_value.tracks.return_value
        tracks.get.return_value.execute.side_effect = lambda: trace.append(('get',)) or {'releases': [{'versionCodes': ['1']}]}
        tracks.update.return_value.execute.side_effect = lambda: trace.append(('update',)) or {}
        argv = ['release', 'promote', '--package', PKG, '--from-track', 'internal', '--to-track', 'production', '--version-code', '1', '--yes', '--confirm', PKG, *option]
        rc, out, err = run(argv, c)
        assert (rc, err) == (0, '')
        sent = tracks.update.call_args.kwargs
        assert sent['packageName'] == PKG and sent['editId'] == 'edit-1' and sent['track'] == 'production'
        tracks.get.assert_called_once_with(packageName=PKG, editId='edit-1', track='internal')
        assert [t[0] for t in trace] == ['create', 'get', 'update', 'commit']
        c._commit_edit.assert_called_once_with(PKG, 'edit-1')
        observed[style] = sent['body']['releases'][0]
    assert observed['query']['status'] == 'completed'
    assert 'userFraction' not in observed['query']
    for style in ('body', 'flag'):
        assert observed[style]['status'] == 'inProgress'
        assert observed[style]['userFraction'] == 0.01
    evidence('F01', final_release_bodies=observed, rc=0, request_order=['create', 'get', 'update', 'commit'])


@pytest.mark.parametrize('variant', ['body', 'query', 'duplicate'])
def test_F02_abbreviated_package_evades_conflict_checks(variant):
    c = MagicMock()
    c.update_listing.return_value = model({'success': True})
    suffix = ['--language', 'en-US', '--title', 'T']
    if variant == 'duplicate':
        argv = ['--package', 'com.a', 'listing', 'update', '--pack', 'com.b', *suffix]
    else:
        # --query is setdefault, so a conflicting query is ignored, not rejected.
        argv = ['--pack', 'com.a', 'listing', 'update', *suffix, '--'+variant, '{"package_name":"com.b"}']
    rc, out, err = run([*argv, '--yes'], c)
    assert (rc, err) == (0, '')
    actual = c.update_listing.call_args.kwargs['package_name']
    assert actual == ('com.a' if variant == 'query' else 'com.b')
    c.update_listing.assert_called_once_with(package_name=actual, language='en-US', title='T', full_description=None, short_description=None, video=None)
    c.reset_mock()
    rc_dry, dry, _ = run(argv, c)
    assert rc_dry == 0 and dry['dry_run'] is True and c.mock_calls == []
    # Full flag spelling must reject both equivalent conflicting inputs.
    full = ['--package' if x == '--pack' else x for x in argv]
    rc_full, _, _ = run([*full, '--yes'], c)
    assert rc_full == 2 and c.mock_calls == []
    evidence('F02', variant=variant, rc=rc, actual_package=actual, full_spelling_rc=rc_full, dry_run_client_calls=0)


def test_F03_listing_get_error_swallowed_then_update_commit():
    observed = {}
    for get_fail in (False, True):
        c, s, trace = client_with_service()
        listings = s.edits.return_value.listings.return_value
        existing = {'title': 'Old', 'shortDescription': 'KEEP short', 'fullDescription': 'KEEP full', 'video': 'https://www.youtube.com/watch?v=example'}
        def get_result():
            trace.append(('get',))
            if get_fail:
                raise http(503)
            return existing
        listings.get.return_value.execute.side_effect = get_result
        listings.update.return_value.execute.side_effect = lambda: trace.append(('update',)) or {}
        argv = ['listing', 'batch-update', '--package', PKG, '--updates', '[{"language":"en-US","title":"New"}]', '--commit', 'true', '--yes']
        rc, out, err = run(argv, c)
        assert (rc, err) == (0, '') and out['success'] is True and out['commit'] is True
        sent = listings.update.call_args.kwargs
        assert {k:sent[k] for k in ('packageName','editId','language')} == {'packageName':PKG,'editId':'edit-1','language':'en-US'}
        listings.get.assert_called_once_with(packageName=PKG, editId='edit-1', language='en-US')
        assert [t[0] for t in trace] == ['create', 'get', 'update', 'commit']
        c._commit_edit.assert_called_once_with(PKG, 'edit-1')
        c._delete_edit.assert_not_called()
        observed['503' if get_fail else 'success'] = sent['body']
    assert observed['503'] == {'title': 'New', 'shortDescription': '', 'fullDescription': ''}
    assert observed['success']['shortDescription'] == 'KEEP short'
    assert 'video' in observed['success'] and 'video' not in observed['503']
    evidence('F03', rc=0, success=True, update_bodies=observed, request_order=['create', 'get-503', 'update', 'commit'])


def test_control_batch_update_error_is_reported():
    c, s, trace = client_with_service()
    listings = s.edits.return_value.listings.return_value
    listings.get.return_value.execute.return_value = {}
    listings.update.return_value.execute.side_effect = http(403)
    rc, out, err = run(['listing', 'batch-update', '--package', PKG, '--updates', '[{"language":"en-US","title":"New"}]', '--commit', 'true', '--yes'], c)
    assert rc == 3 and out is None
    assert json.loads(err)['error']['status'] == 403
    c._commit_edit.assert_not_called()
    c._delete_edit.assert_called_once_with(PKG, 'edit-1')


def test_F04_filtered_duplicate_drops_later_valid_review():
    pages = [
        {'reviews': [raw('r1', False)], 'tokenPagination': {'nextPageToken': 'T2'}},
        {'reviews': [raw('r1'), raw('r2')]},
    ]
    c, request = review_api(pages)
    rc, out, err = run(['review', 'list', '--package', PKG, '--all'], c)
    assert (rc, err) == (0, '')
    ids = [r['review_id'] for r in out]
    assert ids == ['r2']
    assert request.call_args_list == [call(packageName=PKG, maxResults=100), call(packageName=PKG, maxResults=100, token='T2')]
    evidence('F04', rc=rc, expected_valid_ids=['r1','r2'], actual_ids=ids, request_kwargs=[c.kwargs for c in request.call_args_list])


def test_F05_zero_limit_returns_one_review():
    c, request = review_api([{'reviews': [raw('r1'), raw('r2')], 'tokenPagination': {'nextPageToken': 'T2'}}])
    rc, out, err = run(['review', 'list', '--package', PKG, '--all', '--limit', '0'], c)
    assert (rc, err) == (0, '') and len(out) == 1
    request.assert_called_once_with(packageName=PKG, maxResults=100)
    evidence('F05', rc=rc, limit=0, returned_count=len(out), requests=1)


def test_F06_cli_replaces_shared_mcp_provider_same_process():
    import play_store_mcp.server as server
    mcp_client = MagicMock()
    cli_client = object.__new__(CliPlayStoreClient)
    mcp_client.get_reviews.return_value = [model({'review_id':'MCP'})]
    cli_client.get_reviews = MagicMock(return_value=[model({'review_id':'CLI'})])
    # Establish a mocked request-context provider for MCP.
    tools.configure_client(lambda: mcp_client)
    before = server.get_reviews(PKG)
    rc, out, err = run(['listing', 'update', '--package', PKG, '--language', 'en-US', '--title', 'T'], cli_client)
    assert (rc, err) == (0, '') and out['dry_run'] is True
    cli_client.get_reviews.assert_not_called()
    after = server.get_reviews(PKG)
    assert before == [{'review_id':'MCP'}] and after == [{'review_id':'CLI'}]
    assert tools.get_client() is cli_client
    mcp_client.get_reviews.assert_called_once_with(package_name=PKG, max_results=50, translation_language=None)
    cli_client.get_reviews.assert_called_once_with(package_name=PKG, max_results=50, translation_language=None)
    evidence('F06', pre_cli_mcp=before, post_cli_mcp=after, cli_dry_run=True, same_process_only=True, post_client_class=type(tools.get_client()).__name__)


@pytest.mark.parametrize('placement', ['before','between','after','alias'])
def test_control_write_guard_and_confirmation_all_placements(placement):
    c = MagicMock()
    c.reply_to_review.return_value = ReviewReplyResult(success=True, review_id='r1', message='ok')
    flags = ['--package', PKG, '--confirm', PKG]
    tail = ['r1', '--reply-text', 'Thanks']
    paths = {
      'before': [*flags, 'review', 'reply', *tail],
      'between': ['review', *flags, 'reply', *tail],
      'after': ['review', 'reply', *tail, *flags],
      'alias': [*flags, 'reply-to-review', *tail],
    }
    argv = paths[placement]
    rc, out, err = run(argv, c)
    assert (rc, err) == (0,'') and out['dry_run'] is True and c.mock_calls == []
    rc, _, err = run(['--yes', *argv], c)
    assert (rc, err) == (0,'')
    c.reply_to_review.assert_called_once_with(package_name=PKG, review_id='r1', reply_text='Thanks')
    c.reset_mock()
    bad = [*argv, '--yes', '--confirm', 'com.other']
    rc, _, _ = run(bad, c)
    assert rc == 2 and c.mock_calls == []


@pytest.mark.parametrize('opt', ['--body','--query'])
def test_control_explicit_and_positional_json_conflicts(opt):
    for obj in ({'package_name':'com.other'}, {'order_id':'O2'}):
        c = MagicMock()
        rc, _, _ = run(['order', 'refund', 'O1', '--package', PKG, '--confirm', PKG, '--yes', opt, json.dumps(obj)], c)
        assert rc == 2 and c.mock_calls == []


def test_control_developer_confirmation_and_all_write():
    for confirm, expected in [('WRONG',2), ('1',0)]:
        c = MagicMock()
        c.create_user.return_value = model({'success':True})
        rc, _, _ = run(['user', 'create', 'a@example.com', '--body', '{"developer_id":1}', '--confirm', confirm, '--yes'], c)
        assert rc == expected
        if expected == 0:
            c.create_user.assert_called_once_with(developer_id=1, email='a@example.com', access_state='accessGranted')
        else:
            assert c.mock_calls == []
    c = MagicMock()
    rc, _, _ = run(['listing', 'update', '--package', PKG, '--language', 'en-US', '--all', '--yes'], c)
    assert rc == 2 and c.mock_calls == []


def test_control_pagination_229_valid_and_translation_and_overlap():
    pages = [
        {'reviews': [raw('filtered',False), *[raw(f'r{i}') for i in range(99)]], 'tokenPagination': {'nextPageToken':'T2'}},
        {'reviews': [raw(f'r{i}') for i in range(98,198)], 'tokenPagination': {'nextPageToken':'T3'}},
        {'reviews': [raw(f'r{i}') for i in range(198,229)]},
    ]
    c, request = review_api(pages)
    rc, out, err = run(['review', 'list', '--package', PKG, '--all', '--translation-language', 'zh'], c)
    assert (rc, err) == (0,'')
    assert [x['review_id'] for x in out] == [f'r{i}' for i in range(229)]
    assert request.call_args_list == [
       call(packageName=PKG, maxResults=100, translationLanguage='zh'),
       call(packageName=PKG, maxResults=100, token='T2', translationLanguage='zh'),
       call(packageName=PKG, maxResults=100, token='T3', translationLanguage='zh'),
    ]
    evidence('control_229', rc=0, count=len(out), requests=3)


@pytest.mark.parametrize('terminal', [False, True])
def test_control_page_cap_exactly_100(terminal):
    pages = [{'reviews':[raw('r1')], 'tokenPagination':{'nextPageToken':'T'}} for _ in range(100)]
    if terminal:
        pages[-1]['tokenPagination'] = {}
    c, request = review_api(pages)
    rc, out, err = run(['review', 'list', '--package', PKG, '--all'], c)
    assert request.call_count == 100
    assert rc == (0 if terminal else 3)
    if terminal:
        assert len(out) == 1
    else:
        assert out is None and '100 pages' in err
    evidence('control_cap', terminal=terminal, rc=rc, requests=100)


def test_control_limit_counts_valid_unique_items_across_pages():
    pages = [
      {'reviews':[raw('bad',False),raw('r1'),raw('r2')], 'tokenPagination':{'nextPageToken':'T2'}},
      {'reviews':[raw('r2'),raw('r3'),raw('r4')], 'tokenPagination':{'nextPageToken':'T3'}},
    ]
    c, request = review_api(pages)
    rc, out, err = run(['--all','--limit','3','review','list','--package',PKG], c)
    assert (rc,err) == (0,'') and [x['review_id'] for x in out] == ['r1','r2','r3']
    assert request.call_count == 2
    evidence('control_limit', rc=rc, count=3, requests=2)


def test_control_app_details_raises_and_deletes_edit():
    c, s, trace = client_with_service()
    edits = s.edits.return_value
    edits.details.return_value.get.return_value.execute.return_value = {'defaultLanguage':'en-US'}
    edits.listings.return_value.get.return_value.execute.side_effect = http(404)
    rc, out, err = run(['app','get','--package',PKG,'--language','fr-FR'], c)
    assert rc == 3 and out is None and json.loads(err)['error']['status'] == 404
    c._delete_edit.assert_called_once_with(PKG,'edit-1')
    edits.listings.return_value.get.assert_called_once_with(packageName=PKG,editId='edit-1',language='fr-FR')


def test_F07_all_read_does_not_retry_503(monkeypatch):
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    c, request = review_api([http(503), {'reviews': [raw('r1')]}])
    rc, _, err = run(['review','list','--package',PKG,'--all'], c)
    assert rc == 3 and request.call_count == 1
    assert json.loads(err)['error']['status'] == 503
    control = MagicMock()
    control.get_reviews.side_effect = [http(503), [model({'review_id':'r1'})]]
    plain_rc, _, _ = run(['review','list','--package',PKG], control)
    assert plain_rc == 0 and control.get_reviews.call_count == 2
    evidence('F07', all_rc=rc, all_attempts=1, non_all_rc=plain_rc, non_all_attempts=2)
