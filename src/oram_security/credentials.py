"""Provider-agnostic credential storage for ORAM.

Packaged macOS builds use Keychain. Environment variables remain a developer
fallback for local terminal work and CI.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
from dataclasses import dataclass
from typing import Protocol

DEFAULT_KEYCHAIN_SERVICE = "wtf.momoto.oram"

PROVIDER_ENV_KEYS = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "stability": "STABILITY_API_KEY",
    "huggingface": "HF_TOKEN",
    "fal": "FAL_KEY",
    "replicate": "REPLICATE_API_TOKEN",
}


@dataclass(frozen=True)
class CredentialStatus:
    """Safe credential status payload. Never includes a secret value."""

    provider: str
    configured: bool
    source: str
    last_test_status: str = "not_tested"

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "configured": self.configured,
            "source": self.source,
            "last_test_status": self.last_test_status,
        }


class CredentialStore(Protocol):
    """Provider-agnostic credential store interface."""

    source_name: str

    def set_secret(self, provider: str, value: str) -> None:
        ...

    def get_secret(self, provider: str) -> str | None:
        ...

    def delete_secret(self, provider: str) -> None:
        ...

    def has_secret(self, provider: str) -> bool:
        ...

    def status(self, provider: str) -> CredentialStatus:
        configured = self.has_secret(provider)
        return CredentialStatus(
            provider=provider,
            configured=configured,
            source=self.source_name if configured else "none",
        )


def provider_account(provider: str) -> str:
    return f"provider:{provider.strip().lower()}"


class MacOSKeychainCredentialStore:
    """Credential store backed by macOS Keychain Services."""

    source_name = "macos_keychain"

    def __init__(self, service: str = DEFAULT_KEYCHAIN_SERVICE):
        self.service = service

    @property
    def is_supported(self) -> bool:
        return platform.system() == "Darwin" and ctypes.util.find_library("Security") is not None

    def _security(self):
        if not self.is_supported:
            raise RuntimeError("macOS Keychain is not available on this platform")
        security_path = ctypes.util.find_library("Security")
        if not security_path:
            raise RuntimeError("macOS Keychain is not available on this platform")
        return ctypes.cdll.LoadLibrary(security_path)

    def _core_foundation(self):
        core_path = ctypes.util.find_library("CoreFoundation")
        if not core_path:
            return None
        return ctypes.cdll.LoadLibrary(core_path)

    def _find_item(self, provider: str, *, include_password: bool) -> tuple[int, bytes | None, ctypes.c_void_p | None]:
        sec = self._security()
        service = self.service.encode("utf-8")
        account = provider_account(provider).encode("utf-8")
        password_len = ctypes.c_uint32()
        password_data = ctypes.c_void_p()
        item_ref = ctypes.c_void_p()

        sec.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32

        status = sec.SecKeychainFindGenericPassword(
            None,
            len(service),
            service,
            len(account),
            account,
            ctypes.byref(password_len) if include_password else None,
            ctypes.byref(password_data) if include_password else None,
            ctypes.byref(item_ref),
        )

        password = None
        if status == 0 and include_password and password_data.value:
            password = ctypes.string_at(password_data, password_len.value)
            sec.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            sec.SecKeychainItemFreeContent.restype = ctypes.c_int32
            sec.SecKeychainItemFreeContent(None, password_data)

        return status, password, item_ref if item_ref.value else None

    def _release_item(self, item_ref: ctypes.c_void_p | None) -> None:
        if not item_ref:
            return
        core = self._core_foundation()
        if core is None:
            return
        core.CFRelease.argtypes = [ctypes.c_void_p]
        core.CFRelease.restype = None
        core.CFRelease(item_ref)

    def set_secret(self, provider: str, value: str) -> None:
        if not value:
            raise ValueError("credential value cannot be empty")
        sec = self._security()
        service = self.service.encode("utf-8")
        account = provider_account(provider).encode("utf-8")
        secret = value.encode("utf-8")
        status, _, item_ref = self._find_item(provider, include_password=False)

        if status == 0 and item_ref is not None:
            try:
                sec.SecKeychainItemModifyAttributesAndData.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                ]
                sec.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
                update_status = sec.SecKeychainItemModifyAttributesAndData(
                    item_ref,
                    None,
                    len(secret),
                    ctypes.c_char_p(secret),
                )
                if update_status != 0:
                    raise RuntimeError("failed to update credential in macOS Keychain")
            finally:
                self._release_item(item_ref)
            return

        # errSecItemNotFound = -25300
        if status != -25300:
            raise RuntimeError("failed to query macOS Keychain")

        sec.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        sec.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        add_status = sec.SecKeychainAddGenericPassword(
            None,
            len(service),
            service,
            len(account),
            account,
            len(secret),
            ctypes.c_char_p(secret),
            None,
        )
        if add_status != 0:
            raise RuntimeError("failed to write credential to macOS Keychain")

    def get_secret(self, provider: str) -> str | None:
        status, password, item_ref = self._find_item(provider, include_password=True)
        self._release_item(item_ref)
        if status != 0 or password is None:
            return None
        value = password.decode("utf-8").strip()
        return value or None

    def delete_secret(self, provider: str) -> None:
        sec = self._security()
        status, _, item_ref = self._find_item(provider, include_password=False)
        if status == -25300:
            return
        if status != 0 or item_ref is None:
            raise RuntimeError("failed to query macOS Keychain")
        try:
            sec.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
            sec.SecKeychainItemDelete.restype = ctypes.c_int32
            delete_status = sec.SecKeychainItemDelete(item_ref)
            if delete_status != 0:
                raise RuntimeError("failed to delete credential from macOS Keychain")
        finally:
            self._release_item(item_ref)

    def has_secret(self, provider: str) -> bool:
        return self.get_secret(provider) is not None

    def status(self, provider: str) -> CredentialStatus:
        configured = self.has_secret(provider)
        return CredentialStatus(
            provider=provider,
            configured=configured,
            source=self.source_name if configured else "none",
        )


class EnvCredentialStore:
    """Developer fallback backed by process environment variables."""

    source_name = "env"

    def env_key(self, provider: str) -> str:
        normalized = provider.strip().lower()
        return PROVIDER_ENV_KEYS.get(normalized, f"{normalized.upper()}_API_KEY")

    def set_secret(self, provider: str, value: str) -> None:
        os.environ[self.env_key(provider)] = value

    def get_secret(self, provider: str) -> str | None:
        value = os.environ.get(self.env_key(provider), "")
        return value or None

    def delete_secret(self, provider: str) -> None:
        os.environ.pop(self.env_key(provider), None)

    def has_secret(self, provider: str) -> bool:
        return self.get_secret(provider) is not None

    def status(self, provider: str) -> CredentialStatus:
        configured = self.has_secret(provider)
        return CredentialStatus(
            provider=provider,
            configured=configured,
            source=self.source_name if configured else "none",
        )


class MemoryCredentialStore:
    """In-memory credential store for tests."""

    source_name = "memory"

    def __init__(self, initial: dict[str, str] | None = None):
        self._values = {k.lower(): v for k, v in (initial or {}).items()}

    def set_secret(self, provider: str, value: str) -> None:
        self._values[provider.strip().lower()] = value

    def get_secret(self, provider: str) -> str | None:
        return self._values.get(provider.strip().lower()) or None

    def delete_secret(self, provider: str) -> None:
        self._values.pop(provider.strip().lower(), None)

    def has_secret(self, provider: str) -> bool:
        return self.get_secret(provider) is not None

    def status(self, provider: str) -> CredentialStatus:
        configured = self.has_secret(provider)
        return CredentialStatus(
            provider=provider,
            configured=configured,
            source=self.source_name if configured else "none",
        )


class ChainedCredentialStore:
    """Read-through store: write/delete primary, read primary then fallbacks."""

    source_name = "chained"

    def __init__(self, primary: CredentialStore, fallbacks: list[CredentialStore] | None = None):
        self.primary = primary
        self.fallbacks = fallbacks or []

    def _stores(self) -> list[CredentialStore]:
        return [self.primary, *self.fallbacks]

    def set_secret(self, provider: str, value: str) -> None:
        self.primary.set_secret(provider, value)

    def get_secret(self, provider: str) -> str | None:
        for store in self._stores():
            try:
                value = store.get_secret(provider)
            except Exception:
                continue
            if value:
                return value
        return None

    def delete_secret(self, provider: str) -> None:
        self.primary.delete_secret(provider)

    def has_secret(self, provider: str) -> bool:
        return self.get_secret(provider) is not None

    def status(self, provider: str) -> CredentialStatus:
        for store in self._stores():
            try:
                if store.has_secret(provider):
                    return CredentialStatus(provider=provider, configured=True, source=store.source_name)
            except Exception:
                continue
        return CredentialStatus(provider=provider, configured=False, source="none")


def default_credential_store() -> CredentialStore:
    """Return the default local credential store.

    Environment variables override Keychain values so tests, CI, and explicit
    terminal sessions can force a provider key without mutating stored app
    credentials.
    """

    env_store = EnvCredentialStore()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return env_store

    requested = os.environ.get("ORAM_CREDENTIAL_STORE", "").strip().lower()
    if requested == "env":
        return env_store
    if requested == "memory":
        return MemoryCredentialStore()

    keychain = MacOSKeychainCredentialStore()
    if keychain.is_supported:
        return ChainedCredentialStore(env_store, [keychain])
    return env_store


def resolve_provider_secret(provider: str, store: CredentialStore | None = None) -> str | None:
    """Resolve a provider secret without exposing where it was stored."""

    active_store = store or default_credential_store()
    return active_store.get_secret(provider)
