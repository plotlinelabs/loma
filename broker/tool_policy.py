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
    'posthog': {'query': Command('sql')},
}


def prepare_argv(tool: str, argv: list[str], uploads: dict[str, str]) -> list[str]:
    """Validate exact flags and map *only* local-file parameters to uploads."""
    if not argv or argv[0] not in POLICY.get(tool, {}):
        raise Denied()
    spec = POLICY[tool][argv[0]]
    values = set(spec.values.split())
    switches = set(spec.switches.split())
    files = set(spec.files.split())
    csv_files = set(spec.csv_files.split())
    result = [argv[0]]
    seen = set()
    used_uploads = set()
    positional_count = 0
    i = 1
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
        if name in seen or name not in values | switches | files | csv_files:
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
