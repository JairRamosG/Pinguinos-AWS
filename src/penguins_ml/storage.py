"""Acceso a almacenamiento: local o S3.

Un solo lugar para parsear URIs y hablar con S3. El resto del código no
debería saber si lee de un disco o de un bucket: solo pasa una URI.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

S3_SCHEME = "s3://"
_CREDENTIALS_HINT = "Sin credenciales AWS: ejecutá `aws sso login`."


class StorageError(RuntimeError):
    """Error claro al leer o escribir en un almacén."""


def is_s3(uri: str) -> bool:
    return uri.startswith(S3_SCHEME)


def parse_s3(uri: str) -> tuple[str, str]:
    """`s3://bucket/clave` -> `(bucket, clave)`."""
    if not is_s3(uri):
        raise StorageError(f"No es una URI de S3: {uri}")
    bucket, _, key = uri.removeprefix(S3_SCHEME).partition("/")
    if not bucket or not key:
        raise StorageError(f"URI de S3 incompleta (falta bucket/clave): {uri}")
    return bucket, key


def _s3_client() -> Any:
    # Usa las credenciales del entorno: perfil SSO, AWS_PROFILE o un role.
    return boto3.client("s3")


def _s3_error(uri: str, action: str, exc: ClientError) -> StorageError:
    code = exc.response.get("Error", {}).get("Code", "?")
    if code in {"NoSuchKey", "404", "NotFound"}:
        return StorageError(f"No existe el objeto: {uri}")
    if code in {"AccessDenied", "403"}:
        return StorageError(
            f"Sin permiso para {action} {uri}. "
            "Revisá el perfil SSO y el prefijo (raw/, models/)."
        )
    return StorageError(f"No se pudo {action} {uri}: {code}")


@contextmanager
def open_read(uri: str) -> Iterator[Any]:
    """Abre `uri` para lectura binaria, en streaming, local o S3."""
    if not is_s3(uri):
        try:
            fh = open(uri, "rb")
        except OSError as exc:
            raise StorageError(f"No se pudo leer {uri}: {exc}") from exc
        with fh:
            yield fh
        return

    bucket, key = parse_s3(uri)
    try:
        body = _s3_client().get_object(Bucket=bucket, Key=key)["Body"]
    except NoCredentialsError as exc:
        raise StorageError(_CREDENTIALS_HINT) from exc
    except ClientError as exc:
        raise _s3_error(uri, "leer", exc) from exc
    except BotoCoreError as exc:
        raise StorageError(f"No se pudo conectar con S3: {exc}") from exc
    try:
        yield body
    finally:
        body.close()


def read_bytes(uri: str) -> bytes:
    with open_read(uri) as fh:
        return fh.read()


def write_bytes(uri: str, data: bytes) -> None:
    if not is_s3(uri):
        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return

    bucket, key = parse_s3(uri)
    try:
        _s3_client().put_object(Bucket=bucket, Key=key, Body=data)
    except NoCredentialsError as exc:
        raise StorageError(_CREDENTIALS_HINT) from exc
    except ClientError as exc:
        raise _s3_error(uri, "escribir", exc) from exc
    except BotoCoreError as exc:
        raise StorageError(f"No se pudo conectar con S3: {exc}") from exc


def list_dirs(prefix_uri: str) -> list[str]:
    """Prefijos hijos de `s3://bucket/clave/`, del más reciente al más viejo.

    S3 no tiene carpetas: son claves con `/`. Este método usa Delimiter
    para listar solo el primer nivel (los `run_id`).
    """
    prefix = prefix_uri if prefix_uri.endswith("/") else f"{prefix_uri}/"
    bucket, key = parse_s3(prefix)
    try:
        response = _s3_client().list_objects_v2(
            Bucket=bucket, Prefix=key, Delimiter="/"
        )
    except NoCredentialsError as exc:
        raise StorageError(_CREDENTIALS_HINT) from exc
    except ClientError as exc:
        raise _s3_error(prefix, "listar", exc) from exc
    except BotoCoreError as exc:
        raise StorageError(f"No se pudo conectar con S3: {exc}") from exc
    children = [
        f"s3://{bucket}/{item['Prefix']}"
        for item in response.get("CommonPrefixes", [])
    ]
    # Los run_id son timestamps ISO: ordenar lexicográficamente es ordenar por fecha.
    return sorted(children, reverse=True)