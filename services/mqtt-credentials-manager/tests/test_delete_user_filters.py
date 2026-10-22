"""Unit tests for MQTT user deletion helpers (no kubectl)."""

import os
import sys

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import mqtt_credentials_manager as mgr  # noqa: E402


class TestFilterPasswordFile:
    def test_removes_matching_user_line(self):
        content = "alice:hash1\ndevice_t1_d1:hash2\n"
        assert mgr._filter_password_file(content, "alice") == "device_t1_d1:hash2\n"

    def test_empty_when_only_user(self):
        assert mgr._filter_password_file("alice:hash1\n", "alice") == ""


class TestFilterAclUserBlock:
    def test_removes_user_stanza_through_blank_line(self):
        content = (
            "user alice\n"
            "topic read foo/#\n"
            "topic write bar/#\n"
            "\n"
            "user bob\n"
            "topic read baz/#\n"
            "\n"
        )
        expected = "user bob\ntopic read baz/#\n\n"
        assert mgr._filter_acl_user_block(content, "alice") == expected

    def test_no_match_returns_unchanged(self):
        content = "user bob\ntopic read x\n\n"
        assert mgr._filter_acl_user_block(content, "alice") == content
