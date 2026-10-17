import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402
import vault_policy  # noqa: E402

# Shaped like a client API key (68 standard-base64 chars), not a real one.
KEY_A = "Yjk4ZjQyMWMtMWQ2Yi00ZTdhLWIzYzUtOWE4ZjFkMmUzYjRjOjEyMzQ1Njc4OWFiY2RlZjA="
KEY_B = "ZDE3NGY5YjItOGM1ZC00ZmIxLTk3MmUtNGFiYzFkMmUzZjRhOjk4NzY1NDMyMWZlZGNiYTA="
KEY_C = "Nm0xMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM="
VAULT = "/Users/someone/.secrets"


class ParseEntriesTest(unittest.TestCase):
    def test_header_lines_are_labels_without_values_and_dropped(self):
        text = "Example Workspace API Keys\nAll 13 keys verified\nBilling\n%s\n" % KEY_A
        self.assertEqual(vault_policy.parse_entries(text), [("Billing", KEY_A)])

    def test_repeated_label_keeps_last_value_in_first_position(self):
        text = "A\n%s\nB\n%s\nA\n%s\n" % (KEY_A, KEY_B, KEY_C)
        self.assertEqual(vault_policy.parse_entries(text), [("A", KEY_C), ("B", KEY_B)])

    def test_identifier_label_accepts_a_20_char_value(self):
        text = "ACCESS_KEY_ID:id\nAKIAEXAMPLE000000000\n"
        self.assertEqual(vault_policy.parse_entries(text), [("ACCESS_KEY_ID:id", "AKIAEXAMPLE000000000")])

    def test_unmarked_label_refuses_a_20_char_value(self):
        self.assertEqual(vault_policy.parse_entries("ACCESS_KEY_ID\nAKIAEXAMPLE000000000\n"), [])


class ImportTableTest(unittest.TestCase):
    def test_label_words_before_the_value_and_header_rows_skipped(self):
        table = "username credential\nSales 1 (Alice) %s\nSupport 1 %s\n" % (KEY_A, KEY_B)
        self.assertEqual(
            vault_policy.parse_entries(vault_policy.import_table(table)),
            [("Sales 1 (Alice)", KEY_A), ("Support 1", KEY_B)],
        )


class MergeTest(unittest.TestCase):
    def test_replace_by_label_rename_in_place_append_new_keep_rest(self):
        base = "header line\nSales 1 (Bob)\n%s\nBilling\n%s\n" % (KEY_A, KEY_B)
        updates = "Sales 1 (Alice)\n%s\nSupport 1\n%s\n" % (KEY_C, KEY_A)
        text, rows = vault_policy.merge(
            base, updates, {"Sales 1 (Bob)": "Sales 1 (Alice)"}
        )
        self.assertEqual(
            text,
            "header line\nSales 1 (Alice)\n%s\nBilling\n%s\nSupport 1\n%s\n" % (KEY_C, KEY_B, KEY_A),
        )
        self.assertEqual(
            [(label, status) for label, status, _old, _new in rows],
            [("Sales 1 (Alice)", "renamed"), ("Billing", "kept"), ("Support 1", "added")],
        )

    def test_identifier_entry_replaced_by_label(self):
        base = "ACCESS_KEY_ID:id\nAKIAEXAMPLE000000000\nBilling\n%s\n" % KEY_A
        text, rows = vault_policy.merge(base, "ACCESS_KEY_ID:id\nAKIAREPLACED00000000\n")
        self.assertEqual(text, "ACCESS_KEY_ID:id\nAKIAREPLACED00000000\nBilling\n%s\n" % KEY_A)
        self.assertEqual([(label, status) for label, status, _old, _new in rows],
                         [("ACCESS_KEY_ID:id", "replaced"), ("Billing", "kept")])

    def test_rename_target_missing_from_updates_is_an_error(self):
        with self.assertRaises(ValueError):
            vault_policy.merge("A\n%s\n" % KEY_A, "B\n%s\n" % KEY_B, {"A": "C"})


class RedactionTest(unittest.TestCase):
    def test_known_value_masked_everywhere_label_kept(self):
        tokens = vault_policy.vault_tokens(["Support 1\n%s\n" % KEY_A])
        out = gate_policy.redact_secrets("Support 1: %s and again %s" % (KEY_A, KEY_A), tokens)
        masked = vault_policy.mask(KEY_A)
        self.assertEqual(out, "Support 1: %s and again %s" % (masked, masked))

    def test_unknown_key_shaped_token_masked_but_digests_and_paths_kept(self):
        text = (
            "key %s\n"
            "image ghcr.io/acme/example-service:85152016178a7a5dc221ce57538e2f3da191801e\n"
            "path /Users/me/Repos/example-service/tmp/probe/x.txt\n"
            "sha e6ea08ee5c823e0f80ce65a1182d07de4cf22f2883d247027d3b4fd1599b65a9\n"
        ) % KEY_B
        out = gate_policy.redact_secrets(text)
        self.assertNotIn(KEY_B, out)
        self.assertEqual(out.splitlines()[1:], text.splitlines()[1:])

    def test_identifier_masked_by_exact_match_other_20_char_tokens_kept(self):
        tokens = vault_policy.vault_tokens(["ACCESS_KEY_ID:id\nAKIAEXAMPLE000000000\n"])
        out = gate_policy.redact_secrets("ACCESS_KEY_ID:id AKIAEXAMPLE000000000 other AKIAOTHERVALUE111111", tokens)
        self.assertEqual(
            out, "ACCESS_KEY_ID:id %s other AKIAOTHERVALUE111111" % vault_policy.mask("AKIAEXAMPLE000000000"))

    def test_all_digit_identifier_masked_by_exact_match(self):
        value = "12345678901234567890"
        tokens = vault_policy.vault_tokens(["ACCOUNT:id\n%s\n" % value])
        self.assertEqual(gate_policy.redact_secrets("account %s" % value, tokens),
                         "account %s" % vault_policy.mask(value))

    def test_labels_are_not_known_values(self):
        text = "EXAMPLE_SERVICE_TOKEN\n%s\nACCESS_KEY_ID:id\nAKIAEXAMPLE000000000\n" % KEY_A
        self.assertEqual(vault_policy.vault_tokens([text]), {KEY_A, "AKIAEXAMPLE000000000"})

    def test_tree_redaction_keeps_shape(self):
        tokens = {KEY_A}
        value = {"file": {"content": "x %s y" % KEY_A, "numLines": 1}, "list": [KEY_A]}
        self.assertEqual(
            vault_policy.redact_tree(value, tokens),
            {"file": {"content": "x %s y" % vault_policy.mask(KEY_A), "numLines": 1},
             "list": [vault_policy.mask(KEY_A)]},
        )


class AccessGateTest(unittest.TestCase):
    def violation(self, tool, tool_input, cwd="/Users/someone/Documents/Repos/backend", tokens=None):
        return vault_policy.secret_access_violation(
            tool, tool_input, cwd, VAULT, tokens or {KEY_A}, ["/etc/example-keys/keys.txt"]
        )

    def test_bash_naming_the_vault_or_a_hidden_glob_is_refused(self):
        self.assertIsNotNone(self.violation("Bash", {"command": "cat ~/.secrets/acme/x.txt"}))
        self.assertIsNotNone(self.violation("Bash", {"command": "cat ~/.s*/acme/x.txt"}))
        self.assertIsNotNone(self.violation("Bash", {"command": "cat $HOME/.sec?ets/x"}))

    def test_bash_printing_secret_payloads_refused_names_only_allowed(self):
        self.assertIsNotNone(self.violation("Bash", {"command": "kubectl -n ns get secret foo -o yaml"}))
        self.assertIsNotNone(self.violation("Bash", {"command": "kubectl get secrets -n ns foo -ojson | jq"}))
        self.assertIsNotNone(self.violation("Bash", {"command": "kubectl get secret foo -o jsonpath='{.data}'"}))
        self.assertIsNone(self.violation("Bash", {"command": "kubectl -n ns get secret"}))
        self.assertIsNone(self.violation("Bash", {"command": "kubectl -n ns describe secret foo"}))
        self.assertIsNone(self.violation("Bash", {"command": "kubectl -n ns get pods -o wide"}))

    def test_bash_naming_a_protected_pod_path_refused(self):
        self.assertIsNotNone(self.violation(
            "Bash", {"command": "kubectl exec deploy/x -- cat /etc/example-keys/keys.txt"}))

    def test_secretctl_and_ordinary_commands_allowed(self):
        self.assertIsNone(self.violation(
            "Bash", {"command": "secretctl run acme EXAMPLE_SERVICE_TOKEN -- true"},
            tokens=vault_policy.vault_tokens(["EXAMPLE_SERVICE_TOKEN\n%s\n" % KEY_A])))
        self.assertIsNone(self.violation("Bash", {"command": "~/.claude/tools/secretctl list acme/service_keys"}))
        self.assertIsNone(self.violation("Bash", {"command": "rg -n secret src/ | head"}))

    def test_known_value_in_any_tool_input_refused(self):
        self.assertIsNotNone(self.violation("Write", {"file_path": "/tmp/x", "content": "k=%s" % KEY_A}))
        self.assertIsNotNone(self.violation("Agent", {"prompt": "use %s" % KEY_A, "description": "x"}))
        self.assertIsNotNone(self.violation("Bash", {"command": "curl -H 'Authorization: Bearer %s' u" % KEY_A}))

    def test_file_tools_inside_vault_refused_outside_allowed(self):
        self.assertIsNotNone(self.violation("Read", {"file_path": "~/.secrets/acme/x.txt"}, cwd="/Users/someone"))
        self.assertIsNotNone(self.violation("Read", {"file_path": ".secrets/x.txt"}, cwd="/Users/someone"))
        self.assertIsNotNone(self.violation("Edit", {"file_path": VAULT + "/x.txt", "old_string": "a", "new_string": "b"}))
        self.assertIsNone(self.violation("Read", {"file_path": "/Users/someone/.secrets-policy.md"}))

    def test_search_root_containing_the_vault_refused(self):
        self.assertIsNotNone(self.violation("Grep", {"pattern": "x", "path": "/Users/someone"}))
        self.assertIsNotNone(self.violation("Glob", {"pattern": "**/*.txt"}, cwd="/Users/someone"))
        self.assertIsNone(self.violation("Grep", {"pattern": "x", "path": "/Users/someone/Documents"}))


if __name__ == "__main__":
    unittest.main()
