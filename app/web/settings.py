from dataclasses import dataclass
import os
from app.importing.core3_live.exporter_adapter import CountSafetyPolicy


@dataclass(frozen=True)
class WebSettings:
    admin_username: str = ""
    admin_password: str = ""
    api_token: str = ""
    csrf_secret: str = ""
    max_upload_bytes: int = 8 * 1024 * 1024
    freshness_hours: int = 24
    secure_cookies: bool = True
    native_max_reduction_fraction: float = 0.5
    native_review_overrides: bool = False
    native_sustained_max_reduction_fraction: float = 0.5

    def __post_init__(self):
        CountSafetyPolicy(self.native_max_reduction_fraction, self.native_sustained_max_reduction_fraction)
        if self.max_upload_bytes < 1 or self.freshness_hours < 1:
            raise ValueError("Upload size and freshness must be positive")
        if bool(self.admin_username) != bool(self.admin_password):
            raise ValueError("Configure both administrator username and password")
        if self.admin_username and len(self.csrf_secret) < 32:
            raise ValueError("BELLUM_CSRF_SECRET must contain at least 32 characters")

    @classmethod
    def from_env(cls):
        secure = os.getenv("BELLUM_SECURE_COOKIES", "true").lower()
        if secure not in {"true", "false"}:
            raise ValueError("BELLUM_SECURE_COOKIES must be true or false")
        overrides = os.getenv("BELLUM_NATIVE_REVIEW_OVERRIDES", "false").lower()
        if overrides not in {"true", "false"}:
            raise ValueError("BELLUM_NATIVE_REVIEW_OVERRIDES must be true or false")
        return cls(
            admin_username=os.getenv("BELLUM_ADMIN_USERNAME", ""),
            admin_password=os.getenv("BELLUM_ADMIN_PASSWORD", ""),
            api_token=os.getenv("BELLUM_UPLOAD_API_TOKEN", ""),
            csrf_secret=os.getenv("BELLUM_CSRF_SECRET", ""),
            max_upload_bytes=int(os.getenv("BELLUM_MAX_UPLOAD_BYTES", "8388608")),
            freshness_hours=int(os.getenv("BELLUM_SNAPSHOT_FRESHNESS_HOURS", "24")),
            secure_cookies=secure == "true",
            native_max_reduction_fraction=float(os.getenv("BELLUM_NATIVE_MAX_REDUCTION_FRACTION", "0.5")),
            native_review_overrides=overrides == "true",
            native_sustained_max_reduction_fraction=float(os.getenv("BELLUM_NATIVE_SUSTAINED_MAX_REDUCTION_FRACTION", "0.5")),
        )
