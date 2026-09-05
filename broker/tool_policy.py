"""Reviewed command schemas for backend CLI adapters.

A registered tool is NOT permission to execute all of its commands. Unknown
commands/flags fail closed until reviewed here. Local inputs must be uploaded
in this request; the worker can never name an existing backend file. Utilities
that render code or accept unrestricted local directories are not adapters.
"""
from dataclasses import dataclass
from broker.service import Denied


@dataclass(frozen=True)
class Command:
    values: str = ''
    switches: str = ''
    files: str = ''
    csv_files: str = ''
    positionals: int = 0
    repeatable: str = ''
    stdin: bool = False
    outputs: str = ''


POLICY = {
    'gmail': {
        'list-inbox': Command('limit query'),
        'read-email': Command('message-id'),
        'search': Command('query limit'),
        'send-email': Command('to subject body cc html-body', files='html-body-file', csv_files='attachments'),
        'create-draft': Command('to subject body cc thread-id in-reply-to html-body', files='html-body-file', csv_files='attachments'),
    },
    'google_drive': {
        'list-files': Command('query limit'), 'read-file': Command('file-id'),
        'search': Command('query limit'),
        'upload-file': Command('name folder-id mime-type', files='file-path'),
        'create-folder': Command('name parent-id'), 'copy-file': Command('file-id name folder-id'),
    },
    'google_calendar': {
        'list-events': Command('limit time-min time-max'), 'get-event': Command('event-id'),
        'create-event': Command('summary start end description attendees location'),
        'update-event': Command('event-id summary start end description attendees location'),
        'delete-event': Command('event-id'), 'search': Command('query limit'),
    },
    'google_sheets': {
        'get-info': Command('spreadsheet-id'), 'list-sheets': Command('spreadsheet-id'),
        'read-range': Command('spreadsheet-id range'), 'write-range': Command('spreadsheet-id range values'),
        'copy-spreadsheet': Command('spreadsheet-id title'),
    },
    'google_slides': {
        'get-info': Command('presentation-id'), 'list-slides': Command('presentation-id'),
        'read-slide': Command('presentation-id slide-index'), 'create-presentation': Command('title'),
        'add-slide': Command('presentation-id insertion-index layout'),
        'replace-text': Command('presentation-id find replacement', switches='match-case'),
    },
    'google_docs_personal': {
        'get-info': Command('document-id'), 'read-doc': Command('document-id'),
        'create-doc': Command('title content'), 'append-text': Command('document-id text'),
        'insert-text': Command('document-id text index'),
        'replace-text': Command('document-id find replacement', switches='match-case'),
        'copy-doc': Command('document-id title'),
    },
    'google_apps_script': {
        'list-projects': Command('query limit'), 'get-content': Command('script-id'),
        'create-project': Command('title parent-id'),
        'update-content': Command('script-id files-json file-name', files='files-json-file code-file'),
        'create-version': Command('script-id description'), 'list-versions': Command('script-id limit'),
    },
    'slack_user': {
        'read-channel': Command('channel limit'), 'search': Command('query limit'),
        'send-message': Command('channel text thread-ts file-title', files='file'),
        'open-dm': Command('users'), 'react': Command('channel ts emoji'),
        'unreact': Command('channel ts emoji'),
        'upload-file': Command('channels title message', files='file'),
    },
    'notify': {
        'send': Command('title body conversation-id link'), 'list': Command('limit'),
    },
    'grain': {
        'search': Command(positionals=1), 'recent': Command(positionals=1),
        'transcript': Command(switches='text', positionals=1),
    },
    'loma_skills': {
        'list': Command(), 'search': Command('query', positionals=1),
        'get': Command('slug'), 'dump': Command('slug'), 'file': Command('slug path'),
        'asset': Command('slug path'),
        'create': Command('slug', files='skill-md'),
        'update-file': Command('slug path', files='content-file'),
        'upload-asset': Command('slug path', files='file'),
        'delete-file': Command('slug path'),
        # import-dir intentionally absent: it reads a backend directory.
    },
    'ashby': {
        'check-auth': Command(), 'list-jobs': Command('status'),
        'get-job': Command(positionals=1), 'list-stages': Command('job-id'),
        'list-candidates': Command('limit'), 'get-candidate': Command(positionals=1),
        'list-applications': Command('job-id status'), 'get-application': Command(positionals=1),
        'export-application': Command(positionals=1),
        'export-job-applications': Command('job-id status limit'),
        'list-departments': Command(), 'list-locations': Command(), 'list-openings': Command('limit'),
        'create-job': Command('title team-id location-id default-interview-plan-id job-template-id'),
        'update-job': Command('title team-id location-id default-interview-plan-id custom-requisition-id', positionals=1),
        'set-job-status': Command('status', positionals=1),
        'duplicate-job': Command('title', positionals=1),
        'create-opening': Command('identifier description team-id location-ids employment-type job-ids'),
        'add-job-to-opening': Command('opening-id job-id'),
    },
    'sentry': {'projects': Command(), 'daily': Command('days environment', positionals=1),
               'slow': Command('days limit environment', positionals=1)},
    'pylon': {
        'issue': Command(positionals=1), 'messages': Command(positionals=1),
        'threads': Command(positionals=1), 'teams': Command(),
        'reply': Command('to cc', files='attachment', positionals=2, repeatable='to cc attachment', stdin=True),
        'note': Command('thread message', files='attachment', positionals=1, repeatable='attachment', stdin=True),
        'issues': Command('days state team'), 'update': Command('state status', positionals=1),
        'create-thread': Command(positionals=2),
    },
    'grafana': {
        'alerts list': Command('state'), 'alerts rules': Command(),
        'alerts silence': Command('duration comment', positionals=1),
        'alerts history': Command('range', positionals=1),
        'query lag': Command('range time', positionals=1),
        'query state': Command(positionals=1), 'query members': Command(positionals=1),
        'query all-lag': Command(), 'query synthetics': Command('range', positionals=1),
        'query synthetic-logs': Command('range start end', positionals=1),
        'oncall current': Command('schedule'), 'oncall next': Command('schedule'),
        'oncall schedules': Command(),
    },
    'slack_reader': {
        'channels': Command('query'), 'history': Command('limit thread-ts', positionals=1),
        'send': Command('text thread-ts file-title', switches='require-thread', files='file', positionals=1, stdin=True),
    },
    'zoho_books': {
        'get-contact': Command('region', positionals=1),
        'search-contacts': Command('region name status'),
        'search-contacts-by-company': Command('region company status'),
        'list-invoices': Command('region customer-id status'),
        'get-invoice': Command('region', positionals=1),
        'download-invoice-pdf': Command('region', positionals=1, outputs='output'),
        'list-estimates': Command('region customer-id status'),
        'get-estimate': Command('region', positionals=1),
        'download-estimate-pdf': Command('region', positionals=1, outputs='output'),
        'list-credit-notes': Command('region customer-id'),
        'get-credit-note': Command('region', positionals=1),
        'list-payments': Command('region customer-id'), 'get-payment': Command('region', positionals=1),
        'list-recurring-invoices': Command('region customer-id'),
        'send-invoice': Command('region', positionals=1, stdin=True),
        'send-estimate': Command('region', positionals=1, stdin=True),
    },
    'telegram': {'send-message': Command('text'), 'status': Command()},
    'posthog': {
        'projects': Command('project'), 'definitions': Command('project search limit'),
        'definition-properties': Command('project', positionals=1),
        'events': Command('project from to limit filter', positionals=1, repeatable='filter'),
    },
    'github_pr_resolve': {
        'resolve': Command('thread-id'), 'list-unresolved': Command('repo pr'),
    },
    'linear': {'velocity': Command('month'), 'bucket-split': Command('month')},
    'phantombuster': {
        'send': Command(positionals=2), 'message': Command(positionals=2),
        'inbox': Command('type count'), 'status': Command(positionals=1),
    },
    'monetize_now': {
        'search-accounts': Command(positionals=16), 'get-account': Command(positionals=1),
        'get-account-by-custom-id': Command(positionals=1),
        'list-accounts': Command('status page page-size sort'),
        'get-contract': Command(positionals=1),
        'list-contracts': Command('status account-id page page-size sort'),
        'account-contracts': Command('status page page-size sort', positionals=1),
        'get-bill-group': Command(positionals=1),
        'account-bill-groups': Command('status page page-size sort', positionals=1),
        'get-account-bill-group': Command(positionals=2), 'get-invoice': Command(positionals=1),
        'account-invoices': Command('status bill-group-id page page-size sort', positionals=1),
        'bill-group-invoices': Command('page page-size sort', positionals=2),
        'get-subscription': Command(positionals=1),
        'account-subscriptions': Command('billing-status page page-size sort', positionals=1),
        'bill-group-subscriptions': Command('billing-status page page-size sort', positionals=1),
    },
}


def command_spec(tool: str, argv: list[str]) -> tuple[Command, int]:
    if not argv:
        raise Denied()
    name = " ".join(argv[:2]) if len(argv) >= 2 and " ".join(argv[:2]) in POLICY.get(tool, {}) else argv[0]
    if name not in POLICY.get(tool, {}):
        raise Denied()
    return POLICY[tool][name], len(name.split())


def output_paths(tool: str, argv: list[str]) -> list[str]:
    spec, command_length = command_spec(tool, argv)
    flags = {"--" + name for name in spec.outputs.split()}
    paths = []
    for index in range(command_length, len(argv)):
        flag, sep, value = argv[index].partition("=")
        if flag in flags:
            if not sep:
                if index + 1 >= len(argv):
                    raise Denied()
                value = argv[index + 1]
            if not value or len(value) > 4096 or "\0" in value:
                raise Denied()
            paths.append(value)
    return paths


def prepare_argv(tool: str, argv: list[str], uploads: dict[str, str]) -> list[str]:
    """Validate exact flags and map *only* local-file parameters to uploads."""
    spec, command_length = command_spec(tool, argv)
    values = set(spec.values.split())
    switches = set(spec.switches.split())
    files = set(spec.files.split()) | set(spec.outputs.split())
    csv_files = set(spec.csv_files.split())
    result = argv[:command_length]
    seen = set()
    used_uploads = set()
    positional_count = 0
    i = command_length
    while i < len(argv):
        arg = argv[i]
        i += 1
        if '\0' in arg:
            raise Denied()
        if not arg.startswith('--'):
            positional_count += 1
            if arg.startswith('-') or positional_count > spec.positionals:
                raise Denied()
            result.append(arg)
            continue
        name, sep, value = arg[2:].partition('=')
        if (name in seen and name not in spec.repeatable.split()) or name not in values | switches | files | csv_files:
            raise Denied()
        seen.add(name)
        if name in switches:
            if sep:
                raise Denied()
            result.append('--' + name)
            continue
        if not sep:
            if i >= len(argv):
                raise Denied()
            value = argv[i]
            i += 1
        if '\0' in value or value.startswith('--'):
            raise Denied()
        if name in files | csv_files:
            parts = value.split(',') if name in csv_files else [value]
            # Empty optional file values mean no file, not a backend path.
            if value:
                parts = [part.strip() for part in parts]
                if any(part not in uploads for part in parts):
                    raise Denied()
                used_uploads.update(parts)
                value = ','.join(uploads[part] for part in parts)
        result.extend(['--' + name, value])
    if set(uploads) != used_uploads:
        raise Denied()
    return result
