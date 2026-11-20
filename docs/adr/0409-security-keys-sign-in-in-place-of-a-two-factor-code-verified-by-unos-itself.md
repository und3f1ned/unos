---
status: superseded
superseded-by: ADR-0430
date: 2026-11-18
area: auth
decided-by: agent
supersedes: [ADR-0239]
---

# ADR-0409: Security keys sign in in place of a two-factor code, beside the authenticator app, verified by UNOS itself

## Context

Superseded by ADR-0430: only in part, the refused answers that went to the login history only: every refused answer is a `login_failed` warning in the audit log too, a counter that went backwards with its own text; every other rule here stays in force.

The entry `auth.security-keys` asks that a user register a hardware security key (WebAuthn) on the
Account page and that a touch of the key open the session at the code step of the sign-in, as an
alternative to the TOTP code. The owner delegated the design, asking above all that it be safe
(ADR-0013), and no new runtime dependency may come with it: a change of `supervisor/requirements.txt`
rebuilds the image's layers on the dev device's SD card (ADR-0214). WebAuthn binds a key to a host
name (the relying party ID) and to the page's origin, so where those come from decides whether a key
can be used elsewhere; and ADR-0239 says that a change adding a way past the password supersedes it.

## Decision

- **A second factor beside the app, never alone.** A key is registered only while the account has
  two-factor authentication (`users.totp_enabled`; else 409 "Turn on two-factor authentication with
  an authenticator app first: a security key is an alternative to its codes"). At the code step its
  answer takes the place of the app's code or a recovery code (`security_key` of `LoginRequest`, one
  second factor per request, else 422). It is no password-less sign-in: the password comes first,
  in every request. Turning two-factor authentication off deletes the account's keys in the commit
  of its UPDATE (`totp_disabled` says how many went), and turning it on deletes keys left from before
  (a restored database), as recovery codes go (ADR-0238). The steps that ask for a fresh code (turning
  two-factor authentication off, turning SMB on, the requirement of ADR-0386) still take the app's
  code. The requirement for every account counts `totp_enabled` as before: a setup-only account has
  no app, so no key, and sets the app up first.
- **Verified by UNOS's own code** (`supervisor/src/auth/webauthn.py`) with `cryptography`, already a
  dependency, and a CBOR decoder of its own (`supervisor/src/auth/cbor.py`): only unsigned and
  negative integers, byte and text strings, arrays, maps and false, true and null; no tags, floats,
  undefined or indefinite lengths; at most 4 levels deep and 64 items per array or map; every length
  within the input; map keys integers or texts, each once; texts UTF-8. Algorithms offered and
  accepted: ES256 on P-256, EdDSA on Ed25519 and RS256 of 2048 to 4096 bits, nothing else; the
  public key is kept as the COSE_Key the key gave, with its algorithm, and loaded again at each use.
- **Attestation none.** The registration asks for `attestation: "none"`; any format the browser
  still returns is read by the bounded decoder and its statement is not checked. UNOS has no policy
  on makes of keys, verifying attestation needs the vendors' root certificates kept current (the
  FIDO metadata service, over the network), and an attestation tells the server the key's model. What
  the factor rests on, the private key held by the authenticator, is proven at every sign-in by the
  signature over a fresh challenge. So any authenticator counts, a key held in software too (a
  passkey manager, a script): UNOS cannot tell them apart.
- **What is checked.** A registration (`/register/begin`, `/register/finish`): the client data's type
  `webauthn.create`, its challenge (below), its origin equal to the challenge's and the request's,
  no `crossOrigin` true; the authenticator data's RP ID hash, the user-present flag, attested
  credential data whose credential id is the one sent, a key of the three algorithms, extensions only
  as a CBOR map, nothing after them. A sign-in: the type `webauthn.get`, the challenge, the origin and
  RP ID hash of the key's own row, user present, no attested credential data, the signature over the
  authenticator data and the SHA-256 of the client data. User verification (a PIN or a fingerprint)
  is asked as `discouraged` and not required: the password is the first factor, and a key without a
  PIN set would ask the user to make one. The signature counter must go up, unless the stored one
  and the new one are both 0 (keys that keep none); one that does not answers 401 "This security
  key's signature counter went backwards, so it may have been copied: sign in another way and remove
  it" and writes a `login_failed` warning. The new counter and the time of the last use are written
  in the sign-in's own commit, with a compare-and-swap on the counter read.
- **Challenges** (`Challenges` of `webauthn.py`): 32 random bytes, in RAM only, each taken once
  whether its answer is then accepted or not, bound to the ceremony, the user's id and name, the
  origin and the RP ID, and a registration's also to its session. A challenge lives 120 seconds (the
  browser waits 60); a user has at most 4 per ceremony and the server 256 in all, the oldest dropped
  first. Deleting a user forgets its challenges, a restore forgets all (ids may name others), and a
  restart of the supervisor loses them.
- **RP ID and origin.** A registration is taken only from the web UI's own page: its `Origin`,
  required, is `https://` and the request's `Host` (`from_own_origin`, as for the session cookie of
  ADR-0397), else 403 "A security key is registered only from the web UI's own page". The RP ID is
  that host name in lower case, without the port: an IP address (or a host whose last label is a
  number, which browsers read as one) answers 409 "Security keys work only when the web UI is opened
  by the NAS's name, not by an IP address", as browsers refuse WebAuthn there. The row keeps the
  origin and the RP ID (`security_keys.origin`, `rp_id`): a sign-in checks the answer against the
  row's values, never against the request's Host. The code step offers the keys whose origin is the
  request's (`https://` and its Host), with a challenge for that origin, and names in
  `security_key_origins` where the account's keys work; at any other address it issues no challenge.
  So a forged Host header only chooses which keys are offered; an answer made for another origin
  fails. A registration with a forged Host needs the session and the password, and can only add a key
  of whoever sends it. No setting names the NAS: it can be reached under several names, and each key
  works at the one where it was added. The Account page opened by IP address says so and offers no
  Add; the sign-in page there says at which addresses the keys work. Browsers also refuse WebAuthn on
  a page with a certificate error: Caddy's local CA must be trusted, or a stored certificate served
  (ADR-0404).
- **Keys, names, removal.** At most 10 keys per account (409 "You already have 10 security keys, the
  most an account can have: remove one first", also counted after the insert's flush, under SQLite's
  write lock). A name of 1 to 64 characters, without control or format characters, unique within
  the account (409); a credential id of 1 to 1023 bytes, unique across every user (a unique column
  and a count after the flush; 409 "This security key is already registered"); the registration's
  `excludeCredentials` lists the account's keys. `/register/begin` and `DELETE
  /api/v1/auth/security-keys/{id}` take the current password (400 "Current password is incorrect"),
  5 a minute each; registering and removing need a session, not an API token (403, as for API
  tokens, ADR-0253); `GET /api/v1/auth/security-keys` lists the keys without key material. The
  registration checks again in its transaction that two-factor authentication is still on.
- **Lost key.** The app's code and the recovery codes still sign in; the user removes the lost key
  with the password. The emergency reset of an administrator (ADR-0284, `src/emergency.py`) deletes
  its keys in the commit of the reset and says so, and `totp_disabled` names them. No reset of a
  regular user's second factor exists, and this adds none (the owner's decision, ADR-0284).
- **Rate limits of the code step.** A key's answer is a `POST /api/v1/auth/login` like a code and
  counts against its 5 a minute per client (ADR-0010): the password step and the answer are one each.
  A challenge is issued only after the password, only for keys of this address, and each answer
  uses its challenge up.
- **Audit.** `security_key_added` (info) and `security_key_removed` (warning), by the user about
  themselves (ADR-0343), resource `security_key`, with the key's name and its origin; a sign-in with a
  key is `login_success` "Signed in with the security key '<name>'"; a refused answer goes to the
  login history only ("Invalid security key", "Expired security key request"), as a refused code
  does, but a counter that went backwards ("Security key counter went backwards") is also a
  `login_failed` warning.
- **Backups and restores.** The table `security_keys` is new (ADR-0319, `create_all`); a backup holds
  it with the database and a restore brings it back. Its rows are untrusted (ADR-0144): every column
  is read as stored (cast to bytes), and a row that cannot be read is listed with `usable` false,
  can be removed, and signs in nowhere. A restore of a backup without the table gets it from
  `init_db`.
- **The session cookie** (ADR-0397) is unchanged: a sign-in with a key and `cookie` sets it and
  answers the session key, as the other sign-ins do.
- **Kept practices** (this supersedes ADR-0239, whose other practices stay as they were): password
  hashes made with bcrypt, 12 rounds; one fixed JWT algorithm per install; no token before the
  second factor, which for an account with two-factor authentication is a TOTP code accepted once
  (ADR-0067), one of its unused recovery codes (ADR-0238) or a security key's answer to a challenge
  that the password step issued; API tokens stored only as SHA-256 hashes and shown once.
- Web UI: `webui/src/lib/components/account/SecurityKeys.svelte` in the Two-Factor Authentication
  card, `webui/src/routes/login/+page.svelte`'s Use a security key, `webui/src/lib/webauthn.ts`.
- Tests: `supervisor/tests/test_security_keys.py` with a software authenticator built on
  cryptography (`supervisor/tests/webauthn_authenticator.py`); `webui/tests/security-keys.spec.ts`
  with Chromium's virtual authenticator (the WebAuthn domain of the DevTools Protocol).

## Consequences

- An account with two-factor authentication has three ways past the password: the app's code, a
  recovery code and a security key. A key replaces the app at the sign-in only: the user keeps the
  app for the steps that ask for a fresh code.
- A key works only at the address where it was added: a NAS reached as `unos.local` and as
  `nas.home.arpa` needs a key added at each, and none works by IP address. A key added on the
  stand-in at `localhost` works there only.
- A sign-in with a key writes its counter and last use into the database, in the commit that a
  sign-in makes anyway.
- The challenges are lost with a restart, so a code step that began before it is answered "The
  security key request expired or was already used: try again".
- A key held in software counts as much as a hardware key: UNOS checks possession, not the device.
- Turning two-factor authentication off removes every key without the password: it takes a code of
  the app, and an API token with a code can do it, as before (ADR-0386).
- A registration refused after the touch (a name or a key too many, two-factor authentication turned
  off meanwhile) leaves the credential in the authenticator, which a passkey manager lists; the
  Account page refuses a name the account has before it asks for the touch.

## Considered options

- Keys as a second factor of their own, without the app: every step that asks for a fresh code, the
  requirement for every account and the emergency reset would need a key's ceremony too, and an
  account whose only key is lost would have no way back but its recovery codes.
- A WebAuthn library (python-fido2, webauthn): a new runtime dependency, which the batch excludes,
  with more of the protocol than a second factor needs.
- Verifying attestation: see above; it would also refuse keys whose vendor root UNOS does not ship.
- The RP ID from a setting: one more setting to get right, which still cannot cover a NAS under
  several names; keeping the address with each key gives the same protection.
- Requiring user verification: a PIN on top of the password, and keys without one would ask to set
  one up.
- Accepting a counter that goes back, as some servers do: a copied key would go unnoticed.
