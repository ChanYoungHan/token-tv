"""Fail publication if an OCI/Docker image archive contains credential state.

Inspect every layer, including files deleted in later layers. Never unpack an
archive onto the host or print file contents, tokens, or image environment values.
Only application text is inspected for recognizable literal secrets; CLI home
and credential file checks use member names without opening those files.
"""
import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import PurePosixPath


SECRET_ENV = {
    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
    'AWS_SECURITY_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN', 'GIT_AUTH_TOKEN',
    'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'OPENAI_API_KEY',
    'CODEX_API_KEY', 'GROK_OAUTH_TOKEN', 'GROK_API_KEY', 'GROK_AUTH_TOKEN', 'XAI_API_KEY',
}
PRIVATE_DIRS = {'.aws', '.claude', '.codex', '.docker'}
PRIVATE_NAMES = {'auth.json', '.credentials.json', 'credentials.json',
                 '.npmrc', '.netrc', 'id_rsa', 'id_ed25519', 'id_ecdsa'}
# The official Node distribution includes this zero-byte package file.
# Verified against https://nodejs.org/dist/v22.23.3/ and its SHASUMS256.txt.
# Any nonempty file or different .npmrc path fails without reading its contents.
EMPTY_NPM_CONFIG = 'usr/local/lib/node_modules/npm/.npmrc'
SECRETS = [re.compile(pattern.encode()) for pattern in (
    r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
    r'\bsk-(?:ant-[A-Za-z0-9_-]{30,}|proj-[A-Za-z0-9_-]{30,}|[A-Za-z0-9]{32,})',
    r'''["'](?:accessToken|refreshToken|access_token|refresh_token|aws_secret_access_key)["']\s*:\s*["'][A-Za-z0-9_./+=-]{20,}["']''',
)]


class UnsafeImage(ValueError):
    pass


def path_of(name):
    path = PurePosixPath(name)
    if '..' in path.parts:
        raise UnsafeImage('Archive contains a parent-directory path')
    return PurePosixPath(*[part for part in path.parts if part not in ('/', '.')])


def check_literals(value):
    if any(pattern.search(value) for pattern in SECRETS):
        raise UnsafeImage('Recognizable literal credential found (value withheld)')


def check_layer(stream):
    with tarfile.open(fileobj=stream, mode='r|*') as layer:
        for member in layer:
            path = path_of(member.name)
            # Directory skeletons are allowed. Any file/link in runtime /data is not.
            if member.isdir():
                continue
            parts = path.parts
            if (parts and parts[0] == 'data') or any(part in PRIVATE_DIRS for part in parts):
                raise UnsafeImage('Runtime/CLI state present in an image layer (contents withheld)')
            if str(path) == EMPTY_NPM_CONFIG:
                if not member.isfile() or member.size != 0:
                    raise UnsafeImage('Expected an empty public npm package configuration')
                continue
            if (path.name in PRIVATE_NAMES or path.name.startswith('.env')
                    or path.suffix in ('.key', '.p12', '.pfx') or '.sqlite' in path.name):
                raise UnsafeImage('Credential/state filename present in an image layer (contents withheld)')
            application = 'token_tv' in parts or str(path).startswith('tmp/token-tv-build/')
            if application and path.name == 'config.json':
                raise UnsafeImage('Application runtime config present in an image layer')
            if member.isfile() and path.suffix == '.pem':
                # Public CA certificates in the base OS are normal; private keys are not.
                with layer.extractfile(member) as certificate:
                    check_literals(certificate.read(2 * 1024 * 1024))
            if application and member.isfile() and path.suffix in ('.py', '.js', '.html', '.css', '.json', '.md', '.txt'):
                if member.size > 2 * 1024 * 1024:
                    raise UnsafeImage('Application text exceeds the inspection limit')
                with layer.extractfile(member) as source:
                    check_literals(source.read())


def check_config(config, revision):
    runtime = config.get('config') or {}
    for entry in runtime.get('Env') or []:
        key, _, value = entry.partition('=')
        if key.upper() in SECRET_ENV and value:
            raise UnsafeImage('Credential environment value baked into image (value withheld)')
    if runtime.get('User') != 'tokentv':
        raise UnsafeImage('Expected the unprivileged tokentv runtime user')
    if revision and (runtime.get('Labels') or {}).get('org.opencontainers.image.revision') != revision:
        raise UnsafeImage('Image revision does not match this workflow commit')
    check_literals(json.dumps(config).encode())
    return config.get('os', '') + '/' + config.get('architecture', '')


def inspect(path, revision=None, required=()):
    platforms, layers, visited = set(), set(), set()
    with tarfile.open(path, 'r:*') as archive:
        members = {str(path_of(item.name)): item for item in archive.getmembers()}

        def read(name, limit=8 * 1024 * 1024):
            item = members.get(name)
            if item is None or not item.isfile() or item.size > limit:
                raise UnsafeImage('Missing or oversized image metadata')
            with archive.extractfile(item) as stream:
                return stream.read()

        def blob(descriptor):
            digest = descriptor['digest']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise UnsafeImage('Unsupported image digest')
            name = 'blobs/sha256/' + digest.split(':', 1)[1]
            item = members.get(name)
            if item is None or not item.isfile() or item.size != descriptor['size']:
                raise UnsafeImage('Missing or invalid image blob')
            if name not in visited:
                checksum = hashlib.sha256()
                with archive.extractfile(item) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(chunk)
                if checksum.hexdigest() != digest.split(':', 1)[1]:
                    raise UnsafeImage('Image blob digest mismatch')
                visited.add(name)
            return name

        def layer(name):
            if name in layers:
                return
            item = members.get(name)
            if item is None or not item.isfile():
                raise UnsafeImage('Missing image layer')
            with archive.extractfile(item) as stream:
                check_layer(stream)
            layers.add(name)

        def manifest(descriptor):
            name = blob(descriptor)
            value = json.loads(read(name))
            if 'manifests' in value:
                for child in value['manifests']:
                    manifest(child)
            else:
                config = json.loads(read(blob(value['config'])))
                platforms.add(check_config(config, revision))
                for item in value['layers']:
                    layer(blob(item))

        if 'oci-layout' in members:
            for item in json.loads(read('index.json'))['manifests']:
                manifest(item)
        elif 'manifest.json' in members:
            for item in json.loads(read('manifest.json')):
                platforms.add(check_config(json.loads(read(item['Config'])), revision))
                for name in item['Layers']:
                    layer(name)
        else:
            raise UnsafeImage('Expected an OCI or docker image save archive')
    if not platforms or not layers or set(required) - platforms:
        raise UnsafeImage('Required platform or image layers missing')
    print('Image publication gate passed:', ', '.join(sorted(platforms)), ';', len(layers), 'layers inspected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--revision')
    parser.add_argument('--require-platform', action='append', default=[])
    args = parser.parse_args()
    try:
        inspect(args.archive, args.revision, args.require_platform)
    except UnsafeImage as error:
        # These messages are constants defined above, never payload-derived text.
        print('Image publication gate FAILED:', str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError):
        # Archive metadata and parser errors might include payload values.
        print('Image publication gate FAILED. Credential/state, metadata, or archive policy violation; no payload printed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
