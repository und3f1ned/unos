---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0122]
---

# ADR-0263: SMB-bound passwords need at least 8 characters, at most 72 bytes, no control characters, and must not be common passwords

## Context

ADR-0122 set the rules of SMB-bound passwords and still allowed common ones, an accepted risk of ADR-0102. SMB has no second factor and no login limit (ADR-0264), so a common password is the first that anyone on the LAN tries (`shares.smb-login-protection`).

## Decision

- Every password that UNOS gives to Samba must pass `constants.smb_password_problem` (`supervisor/src/shares/constants.py`), whose rules come in this order: at least 8 characters, at most 72 bytes in UTF-8, valid Unicode text, no control characters such as tabs or line breaks, and not a common password. Otherwise the answer is 400 with the rule, for example "SMB passwords must have at least 8 characters" or `COMMON_PASSWORD_RULE`, "SMB passwords must not be common passwords: this one is on the list of passwords that attackers try first". No answer carries the password (ADR-0125).
- A password is common when its casefolded form is a line of `supervisor/src/shares/common_passwords.txt`: about 1450 passwords compiled for UNOS, from the passwords that public lists of the most used passwords rank high, keyboard and number patterns, and words with the digits, years and signs that people add to them; only those with at least 8 characters, in lower case and sorted, under UNOS's licence (ADR-0007). The file ships with the code in the supervisor's image.
- The rules are checked by every activation (with an administrator's password, and the user's own with the UNOS password), by the creation of a user with `smb_enabled` before anything is created, by the password change of an active account (the user's own and an administrator's), and by `accounts.set_samba_password` itself. One password is checked without the list: the one that goes back to Samba when a user's own password change fails after `smbpasswd` ran (`_compensate_password`, ADR-0124). It is the password Samba held, checked with `restoring=True` by the other rules, so that an account that got a common password before the list keeps SMB when a change away from it fails.
- The UNOS-wide policy is unchanged: users without SMB may have common passwords, which the web UI and the API take, and shorter ones, which only the API takes (the 8-character minimum for UNOS passwords exists only in the web UI); their web sign-in has its rate limit and two-factor authentication (ADR-0010). A user whose UNOS password breaks a rule changes it before turning SMB on; the Account page says so after every answer that starts with "SMB passwords must".
- Tests: `supervisor/tests/test_smb_models.py` (the rules, `restoring` and the list), `supervisor/tests/test_smb_accounts.py` (TestCommonPasswords: the flows; TestCommitFailureAfterSmbpasswd and TestSmbpasswdTimeout: a common password goes back after a failed change), `webui/tests/users.spec.ts` and `webui/tests/account-smb.spec.ts` (the reason in the Users and Account pages).

## Consequences

- A password of the list, in any letter case, cannot turn SMB on through UNOS and cannot become the password of an active account through UNOS; a password that only contains one, such as `password-for-the-nas`, is taken.
- UNOS checks only the passwords that it gives to Samba. Samba's own password change (SAMR) still takes any password, a common one too, without UNOS, until `shares.password-changes-only-through-unos` refuses such changes (ADR-0250).
- An account that got its password before the list keeps it until its next change, whether or not it is on the list: UNOS cannot read Samba's passwords to check them, and a change that fails puts it back.
- The list stops the most common choices, not every guess: attempts at other passwords stay unlimited until the other step of `shares.smb-login-protection` limits them (ADR-0264).
- Adding or removing a password is a change of the file; its test keeps it sorted, in lower case and without entries shorter than 8 characters.
