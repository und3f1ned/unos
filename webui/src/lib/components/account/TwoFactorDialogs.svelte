<script lang="ts">
  import { AlertCircle, Copy, Lock } from "lucide-svelte";
  import { loadCurrentUser } from "$lib/stores/auth";
  import { disableTOTP, enableTOTP } from "$lib/api/auth";
  import { Alert, Button, HelpText, toast } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import TotpCodeDialog from "./TotpCodeDialog.svelte";
  import type { TwoFactor } from "./twoFactor.svelte";

  // The dialogs of the two-factor card: the setup with its QR code and secret, and turning it off.
  type Props = {
    twoFactor: TwoFactor;
    // Two-factor authentication turned on or off.
    onchange?: () => void;
  };

  let { twoFactor, onchange }: Props = $props();

  async function confirmEnable() {
    if (twoFactor.code.length !== 6) {
      twoFactor.error = $t("account.totp.codeRequired");
      return;
    }

    twoFactor.error = "";
    twoFactor.loading = true;
    try {
      await enableTOTP(twoFactor.code);
      twoFactor.enabled = true;
      toast.success($t("account.totp.enabled"));
      onchange?.();
      twoFactor.setupOpen = false;
      twoFactor.setupData = null;
      twoFactor.qrDataUrl = "";
      twoFactor.code = "";
    } catch (e) {
      twoFactor.error = (e as Error).message || $t("account.totp.invalidCode");
    } finally {
      twoFactor.loading = false;
    }
  }

  async function confirmDisable() {
    if (twoFactor.disableCode.length !== 6) {
      twoFactor.error = $t("account.totp.codeRequired");
      return;
    }

    twoFactor.error = "";
    twoFactor.loading = true;
    try {
      await disableTOTP(twoFactor.disableCode);
      twoFactor.enabled = false;
      toast.success($t("account.totp.disabled"));
      onchange?.();
      twoFactor.disableOpen = false;
      twoFactor.disableCode = "";
      // While it is required, an account past its grace period can now only set it up again: the
      // account, loaded again, says so, and the layout leads to the setup page.
      if (twoFactor.required) void loadCurrentUser().catch(() => {});
    } catch (e) {
      twoFactor.error = (e as Error).message || $t("account.totp.invalidCode");
    } finally {
      twoFactor.loading = false;
    }
  }

  function cancelSetup() {
    twoFactor.setupOpen = false;
    twoFactor.setupData = null;
    twoFactor.qrDataUrl = "";
    twoFactor.code = "";
    twoFactor.error = "";
  }

  function cancelDisable() {
    twoFactor.disableOpen = false;
    twoFactor.disableCode = "";
    twoFactor.error = "";
  }

  async function copySecret() {
    if (twoFactor.setupData?.secret) {
      await navigator.clipboard.writeText(twoFactor.setupData.secret);
      toast.success($t("account.totp.copied"));
    }
  }
</script>

<!-- TOTP Setup Modal -->
{#if twoFactor.setupOpen && twoFactor.setupData}
  {@const setupData = twoFactor.setupData}
  <TotpCodeDialog
    title={$t("account.totp.setupTitle")}
    icon={Lock}
    inputId="totpVerify"
    label={$t("account.totp.verifyLabel")}
    bind:code={twoFactor.code}
    error={twoFactor.error}
    loading={twoFactor.loading}
    confirmLabel={$t("account.totp.enable2fa")}
    oncancel={cancelSetup}
    onconfirm={confirmEnable}
  >
    <p class="text-sm text-content-secondary">
      {$t("account.totp.scan")}
    </p>

    {#if twoFactor.qrDataUrl}
      <div class="flex justify-center">
        <div class="bg-content-on-brand p-2 rounded-lg">
          <img src={twoFactor.qrDataUrl} alt={$t("account.totp.qrAlt")} class="w-48 h-48" />
        </div>
      </div>
    {/if}

    <div class="text-center">
      <HelpText text={$t("account.totp.manual")} class="mb-2" />
      <div class="flex items-center justify-center gap-2">
        <code class="bg-surface-overlay px-3 py-1 rounded font-mono text-sm">
          {setupData.secret}
        </code>
        <Button variant="ghost" size="icon-sm" type="button" onclick={copySecret} title={$t("account.totp.copy")}>
          <Copy class="w-4 h-4" aria-hidden="true" />
          <span class="sr-only">{$t("account.totp.copy")}</span>
        </Button>
      </div>
    </div>
  </TotpCodeDialog>
{/if}

<!-- Disable 2FA Modal -->
{#if twoFactor.disableOpen}
  <TotpCodeDialog
    title={$t("account.totp.disableTitle")}
    icon={AlertCircle}
    danger
    inputId="disableCode"
    label={$t("account.totp.codeLabel")}
    bind:code={twoFactor.disableCode}
    error={twoFactor.error}
    loading={twoFactor.loading}
    confirmLabel={$t("account.totp.disable2fa")}
    oncancel={cancelDisable}
    onconfirm={confirmDisable}
  >
    <p class="text-sm text-content-secondary">
      {$t("account.totp.disableHint")}
    </p>
    {#if twoFactor.required}
      <Alert variant="warning">{$t("twofactor.disableRequired")}</Alert>
    {/if}
  </TotpCodeDialog>
{/if}
