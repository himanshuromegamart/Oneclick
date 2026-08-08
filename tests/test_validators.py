"""Input validation.

These run without a database - they are pure functions, and they guard the
boundary every other layer trusts.
"""

from __future__ import annotations

import pytest

from apps.core.exceptions import ValidationFailed
from apps.core.validators import (
    normalize_phone_number,
    validate_node_name,
    validate_tags,
    validate_upload,
)


class TestPhoneNormalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "9876543210",
            "+919876543210",
            "919876543210",
            "09876543210",
            "+91 98765 43210",
            "98765-43210",
            "(98765) 43210",
            "0091 9876543210",
        ],
    )
    def test_every_common_format_collapses_to_one(self, raw: str):
        # This is what makes the unique constraint on phone_number meaningful:
        # a user cannot be created twice under two spellings of one number.
        assert normalize_phone_number(raw) == "+919876543210"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "123",
            "5876543210",  # Indian mobiles never start below 6
            "98765432101",  # 11 digits
            "987654321",  # 9 digits
            "abcdefghij",
            "+1 555 123 4567",  # not an Indian number
        ],
    )
    def test_invalid_numbers_are_rejected(self, raw: str):
        with pytest.raises(ValidationFailed):
            normalize_phone_number(raw)


class TestNodeNames:
    def test_whitespace_is_trimmed(self):
        assert validate_node_name("  Water ATM  ") == "Water ATM"

    @pytest.mark.parametrize("name", ["a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a|b"])
    def test_path_characters_are_rejected(self, name: str):
        # These break on at least one of Windows, macOS or the mobile clients.
        with pytest.raises(ValidationFailed):
            validate_node_name(name)

    @pytest.mark.parametrize("name", [".", "..", "", "   "])
    def test_reserved_and_empty_names_are_rejected(self, name: str):
        with pytest.raises(ValidationFailed):
            validate_node_name(name)

    def test_windows_device_names_are_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_node_name("CON")

    def test_trailing_dot_is_rejected(self):
        # Windows silently strips these, which would desync name and storage.
        with pytest.raises(ValidationFailed):
            validate_node_name("report.")

    def test_overlong_name_is_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_node_name("x" * 256)

    def test_unicode_is_preserved(self):
        assert validate_node_name("à¤ªà¥à¤°à¤®à¤¾à¤£à¤ªà¤¤à¥à¤°") == "à¤ªà¥à¤°à¤®à¤¾à¤£à¤ªà¤¤à¥à¤°"


class TestUploads:
    def test_accepts_a_normal_document(self):
        name, extension = validate_upload("Price List.pdf", 2048, "application/pdf")
        assert (name, extension) == ("Price List.pdf", "pdf")

    @pytest.mark.parametrize("filename", ["payload.exe", "run.bat", "shell.sh", "x.js"])
    def test_executable_extensions_are_blocked(self, filename: str):
        # The deny list wins even if the extension were somehow allow-listed.
        with pytest.raises(ValidationFailed):
            validate_upload(filename, 1024)

    def test_unlisted_extension_is_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_upload("archive.xyz", 1024)

    def test_extension_check_ignores_declared_content_type(self):
        # A forged Content-Type must not get an executable through.
        with pytest.raises(ValidationFailed):
            validate_upload("malware.exe", 1024, content_type="application/pdf")

    def test_oversize_upload_is_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_upload("huge.mp4", 500 * 1024 * 1024)

    def test_empty_upload_is_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_upload("empty.pdf", 0)

    def test_missing_extension_is_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_upload("README", 100)


class TestTags:
    def test_tags_are_lowercased_and_deduplicated(self):
        assert validate_tags(["ISO", "iso", " Certificate "]) == ["iso", "certificate"]

    def test_empty_input_gives_empty_list(self):
        assert validate_tags(None) == []
        assert validate_tags([]) == []

    def test_too_many_tags_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_tags([f"tag{i}" for i in range(26)])

    def test_overlong_tag_rejected(self):
        with pytest.raises(ValidationFailed):
            validate_tags(["x" * 51])
