<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { login, getAuthStatus, getMe, type SecurityKeyRequest, type SignIn } from "$lib/api/auth";
  import { ApiError } from "$lib/api/client";
  import {
    user,
    isLoggedIn,
    isSetupOnly,
    forgetFailedSignOut,
    signOutAgain as endUnconfirmedSession,
    signOutFailed,
    TWO_FACTOR_SETUP_PATH,
  } from "$lib/stores/auth";
  import { connectWebSocket } from "$lib/stores/stats";
  import { Alert, Button } from "$lib/components/ui";
  import { AuthCard, FormError, PasswordStep, RecoveryCodeStep, TotpStep } from "$lib/components/auth";
  import { t } from "$lib/i18n";
  import { browserErrorKey, getAssertion } from "$lib/webauthn";

  let username = $state("");
  let password = $state("");
  let totpCode = $state("");
  let error = $state("");
  let loading = $state(false);
  let requiresTOTP = $state(false);
  // At the code step: a recovery code in place of the authenticator app's code.
  let useRecoveryCode = $state(false);
  let recoveryCode = $state("");
  // At the code step (ADR-0409): the account has security keys that work at this address, the
  // challenge of the password step while it is unused and fresh, and where its keys work.
  let keysHere = $state(false);
  let keyRequest: SecurityKeyRequest | null = null;
  let keyRequestAt = 0;
  let keyOrigins = $state<string[]>([]);
  let usingKey = $state(false);
  // A challenge lives two minutes on the server and is used up by the answer it gets: an older one,
  // or a try after an answer, takes a new password step. 50 s and the browser's 60 s for the touch
  // stay within the two minutes.
  const KEY_REQUEST_FRESH_MS = 50_000;

  onMount(async () => {
    try {
      const status = await getAuthStatus();
      if (status.setup_required) {
        goto("/setup");
      }
    } catch {
      // API not available
    }
  });

  async function handleSubmit() {
    error = "";
    loading = true;

    try {
      const result = await login(
        username,
        password,
        requiresTOTP && !useRecoveryCode ? totpCode : undefined,
        requiresTOTP && useRecoveryCode ? recoveryCode : undefined
      );

      if (result.requires_totp) {
        // 2FA is required, show TOTP input
        requiresTOTP = true;
        codeStep(result);
        loading = false;
        return;
      }

      await signedIn();
    } catch (e) {
      failed(e);
    } finally {
      loading = false;
    }
  }

  /** What the code step offers besides the app's code. */
  function codeStep(result: SignIn) {
    keyRequest = result.security_key ?? null;
    keyRequestAt = Date.now();
    keysHere = keyRequest !== null;
    keyOrigins = result.security_key_origins ?? [];
  }

  async function signedIn() {
    // Signed in: the session cookie replaced one that a failed sign-out left.
    forgetFailedSignOut();
    const me = await getMe();
    user.set(me);
    isLoggedIn.set(true);
    // No socket serves an account that can only set up two-factor authentication.
    if (!isSetupOnly(me)) connectWebSocket();
    // While every account must have two-factor authentication, one without it is asked to set
    // it up at each sign-in, and after its grace period can only do that.
    goto(me.two_factor_required ? TWO_FACTOR_SETUP_PATH : "/");
  }

  function failed(e: unknown) {
    const key = browserErrorKey(e);
    error = key ? $t(key) : (e as Error).message || $t("login.failed");
    // Only a rejected password leads back to the password form. An invalid
    // or already used code ("Invalid 2FA code", "This two-factor code was
    // already used; wait for the next one", "Invalid recovery code"), a refused
    // security key ("Invalid security key"), too many attempts (429) or a server
    // error keep the 2FA step.
    if (e instanceof ApiError && e.status === 401 && !/2FA|two-factor|recovery code|security key/i.test(error)) {
      requiresTOTP = false;
    }
  }

  /** Sign in with a touch of a security key, in place of the app's code. */
  async function useSecurityKey() {
    if (usingKey || loading) return;
    error = "";
    usingKey = true;
    try {
      let request = keyRequest;
      if (!request || Date.now() - keyRequestAt > KEY_REQUEST_FRESH_MS) {
        const again = await login(username, password);
        if (!again.requires_totp) {
          await signedIn();
          return;
        }
        codeStep(again);
        request = keyRequest;
        if (!request) return;
      }
      const answer = await getAssertion(request);
      // Sent: the server uses the challenge up, whatever it answers.
      keyRequest = null;
      await login(username, password, undefined, undefined, answer);
      await signedIn();
    } catch (e) {
      failed(e);
    } finally {
      usingKey = false;
    }
  }

  function resetForm() {
    requiresTOTP = false;
    totpCode = "";
    useRecoveryCode = false;
    recoveryCode = "";
    keysHere = false;
    keyRequest = null;
    keyOrigins = [];
    error = "";
  }

  let signingOut = $state(false);

  async function signOutAgain() {
    signingOut = true;
    try {
      await endUnconfirmedSession();
    } finally {
      signingOut = false;
    }
  }

  function switchCode() {
    useRecoveryCode = !useRecoveryCode;
    totpCode = "";
    recoveryCode = "";
    error = "";
  }
</script>

<AuthCard>
  {#if $signOutFailed}
    <!-- The page cannot delete the session cookie: only the server's answer to the sign-out does
         (ADR-0397). -->
    <Alert variant="warning" class="mb-4">
      <p>{$t("login.signOutFailed")}</p>
      <Button variant="secondary" size="sm" class="mt-2" loading={signingOut} onclick={signOutAgain}>
        {$t("login.signOutAgain")}
      </Button>
    </Alert>
  {/if}

  <form
    onsubmit={(event) => {
      event.preventDefault();
      handleSubmit();
    }}
    class="space-y-4"
  >
    {#if error}
      <FormError message={error} />
    {/if}

    {#if !requiresTOTP}
      <PasswordStep bind:username bind:password />
    {:else if useRecoveryCode}
      <RecoveryCodeStep bind:code={recoveryCode} />
    {:else}
      <TotpStep bind:code={totpCode} {keysHere} {keyOrigins} {usingKey} {loading} onusekey={useSecurityKey} />
    {/if}

    {#if requiresTOTP}
      <Button variant="ghost" class="w-full" onclick={switchCode}>
        {useRecoveryCode ? $t("login.useAppCode") : $t("login.useRecoveryCode")}
      </Button>

      <Button variant="plain"
        type="button"
        onclick={resetForm}
        class="w-full text-sm text-content-secondary hover:text-content-soft"
      >
        {$t("login.back")}
      </Button>
    {/if}

    <Button variant="primary" class="w-full" type="submit" {loading} disabled={usingKey}>
      {requiresTOTP ? $t("login.verify") : $t("login.signIn")}
    </Button>
  </form>
</AuthCard>
