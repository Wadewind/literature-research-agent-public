"""public-egress 固定档案与声明来源目标边界。"""

import pytest

from literature_agent.domain.agent_network import (
    RESEARCH_PUBLIC_EGRESS_PROFILE,
    FormalSourceValidationError,
    create_research_public_egress_profile,
    normalize_formal_public_source,
    validate_formal_public_source_addresses,
)


def test_public_egress_profile_has_stable_canonical_hash_and_preserves_loopback() -> None:
    profile = create_research_public_egress_profile()

    assert profile == RESEARCH_PUBLIC_EGRESS_PROFILE
    assert profile.default_action == "allow"
    assert profile.allow_namespace_loopback is True
    assert "127.0.0.0/8" not in profile.denied_non_loopback_cidrs
    assert "10.0.0.0/8" in profile.denied_non_loopback_cidrs
    assert profile.profile_hash == (
        "7794c40af8722f8c37a826994a27fb2a378f04b8e0395106ecd2c73544eeda11"
    )


@pytest.mark.parametrize(
    "value",
    (
        "http://localhost/a",
        "http://service.localhost/a",
        "http://127.0.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/a",
        "http://[::1]/a",
        "file:///etc/passwd",
        "https://user:secret@example.com/a",
        "https://example.com/a#fragment",
    ),
)
def test_formal_source_rejects_local_private_metadata_and_unsafe_urls(value: str) -> None:
    with pytest.raises(FormalSourceValidationError):
        normalize_formal_public_source(value)


def test_formal_source_rejects_mixed_dns_answers() -> None:
    source = normalize_formal_public_source("HTTPS://Example.COM/paper?q=1")

    assert source.url == "https://example.com/paper"
    with pytest.raises(FormalSourceValidationError) as exc_info:
        validate_formal_public_source_addresses(source, ("93.184.216.34", "127.0.0.1"))
    assert exc_info.value.code == "source_target_forbidden"


def test_formal_source_accepts_only_public_dns_answers() -> None:
    source = normalize_formal_public_source("https://arxiv.org/abs/2401.00001")
    validate_formal_public_source_addresses(source, ("151.101.3.42",))
