#!/usr/bin/env python3
"""Provision only Reset Radar's private SLS resources with the caller's CLI identity."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PROJECT = 'reset-radar-chenyi-sg'
REGION = 'ap-southeast-1'
GROUP = 'radar-ecs'
IDENTITY = 'reset-radar-sg'


class APIError(RuntimeError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))


def call(action, **parameters):
    command = [os.environ.get('ALIYUN_BIN', 'aliyun')]
    for flag, variable in [('config-path', 'ALIYUN_CONFIG_FILE'), ('profile', 'ALIYUN_PROFILE')]:
        if os.environ.get(variable):
            command += ['--' + flag, os.environ[variable]]
    command += ['sls', action, '--region', REGION, '--project', PROJECT]
    if action == 'create-project':
        command = command[:-2]
    for flag, value in parameters.items():
        command += ['--' + flag.replace('_', '-'), json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if result.returncode and action in ('create-saved-search', 'update-saved-search') and any(message in result.stderr for message in ('--topic is required', 'topic is lacking or error type')):
        # CLI 3.3.19 rejects the API's valid empty topic. Only this operation uses the official SDK.
        from aliyun.log import LogClient
        path = Path(os.environ.get('ALIYUN_CONFIG_FILE', '~/.aliyun/config.json')).expanduser()
        config = json.loads(path.read_text())
        profile = next(p for p in config['profiles'] if p['name'] == os.environ.get('ALIYUN_PROFILE', config['current']))
        if profile['mode'] != 'AK':
            raise RuntimeError('Saved-search workaround currently requires an existing AK CLI profile')
        client = LogClient('https://' + REGION + '.log.aliyuncs.com', profile['access_key_id'], profile['access_key_secret'])
        detail = dict(savedsearchName=parameters['savedsearch_name'], displayName=parameters['display_name'],
                      logstore=parameters['logstore'], searchQuery=parameters['search_query'], topic='')
        method = client.create_savedsearch if action == 'create-saved-search' else client.update_savedsearch
        method(PROJECT, detail)
        return {}
    if result.returncode:
        code = re.search(r'(?:^|\n)\s*Code:\s*([A-Za-z0-9_.-]+)', result.stderr)
        message = re.search(r'(?:^|\n)\s*Message:\s*([^\n]+)', result.stderr)
        raise APIError(code.group(1) if code else action + ' failed (check CLI login/parameters)', message.group(1)[:350] if message else '')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def exists(action, **parameters):
    try:
        return call(action, **parameters)
    except APIError as error:
        if error.code.endswith(('NotExist', 'NotFound')):
            return None
        raise


def configure(bind=False):
    project = exists('get-project')
    if project is None:
        call('create-project', project_name=PROJECT, description='Reset Radar private application observability')
    elif project.get('description') != 'Reset Radar private application observability':
        raise RuntimeError('Refusing to modify a project not owned by this deployment')
    keys = json.loads((ROOT / 'deploy/observability/index.json').read_text())
    configs = json.loads((ROOT / 'deploy/observability/collectors.json').read_text())
    for name, config in configs.items():
        store = exists('get-log-store', logstore=name)
        if store is None:
            call('create-log-store', logstore_name=name, ttl=30, shard_count=1, biz_mode='standard', auto_split=False, enable_tracking=False)
        elif store.get('ttl') != 30 or store.get('mode') != 'standard' or store.get('enable_tracking'):
            raise RuntimeError('Unexpected existing Logstore settings: ' + name)
        call('update-log-store', logstore=name, logstore_name=name, ttl=30, hot_ttl=30, auto_split=False, enable_tracking=False, append_meta=True)
        if call('get-log-store-metering-mode', logstore=name).get('meteringMode') != 'ChargeByDataIngest':
            call('update-log-store-metering-mode', logstore=name, metering_mode='ChargeByDataIngest')
        index = exists('get-index', logstore=name)
        call('update-index' if index is not None else 'create-index', logstore=name, keys=keys,
             line={'token': [' ', '\n', '\t', ',', ';', '(', ')', '[', ']', '{', '}', '"'], 'caseSensitive': False, 'chn': True})
        existing = exists('get-config', config_name='radar-' + name)
        call('update-config' if existing is not None else 'create-config', config_name='radar-' + name,
             input_type=config['input_type'], input_detail=config['input_detail'], output_type='LogService',
             output_detail={'logstoreName': name, 'endpoint': REGION + '-internal.log.aliyuncs.com', 'region': REGION})
    if exists('get-machine-group', machine_group=GROUP) is None:
        call('create-machine-group', group_name=GROUP, machine_identify_type='userdefined', machine_list=IDENTITY)
    if bind:
        for name in configs:
            call('apply-config-to-machine-group', machine_group=GROUP, config_name='radar-' + name)
    for item in json.loads((ROOT / 'deploy/observability/queries.json').read_text()):
        existing = exists('get-saved-search', savedsearch_name=item['name'])
        call('update-saved-search' if existing is not None else 'create-saved-search',
             savedsearch_name=item['name'], display_name=item['title'], logstore=item['store'],
             search_query=item['query'], topic='')
    dashboard = json.loads((ROOT / 'deploy/observability/dashboard.json').read_text())
    existing = exists('get-dashboard', dashboard_name=dashboard['dashboard_name'])
    call('update-dashboard' if existing is not None else 'create-dashboard', **dashboard)
    print(json.dumps({'project': PROJECT, 'region': REGION, 'stores': list(configs), 'bound': bind}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create/update the named private resources')
    parser.add_argument('--bind', action='store_true', help='Start collection after the reviewed application is deployed')
    args = parser.parse_args()
    if not args.apply:
        parser.exit(message='Dry run: project reset-radar-chenyi-sg; access/app, Standard, 30 days, ChargeByDataIngest. Use --apply to configure.\n')
    configure(args.bind)
