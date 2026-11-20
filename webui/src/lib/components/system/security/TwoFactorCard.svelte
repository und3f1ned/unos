<script lang="ts">
  import { toDataURL } from "qrcode";
  import { CheckCircle, Smartphone } from "lucide-svelte";
  import RecoveryCodes from "$lib/components/account/RecoveryCodes.svelte";
  import SecurityKeys from "$lib/components/account/SecurityKeys.svelte";
  import { setupTOTP } from "$lib/api/auth";
  import { Alert, Button, Skeleton } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import Notice from "./Notice.svelte";
  import type { TwoFactor } from "./twoFactor.svelte";

  // The account's two-factor authentication: its status, and Enable or Disable, which open the
  // dialogs of TwoFactorDialogs.
  type Props = {
    twoFactor: TwoFactor;
  };

  let { twoFactor }: Props = $props();

  function openDisable() {
    twoFactor.disableOpen = true;
    twoFactor.error = "";
  }

  async function startSetup() {
    twoFactor.error = "";
    twoFactor.loading = true;
    try {
      const data = await setupTOTP();
      twoFactor.setupData = data;
      try {
        // Twice the displayed size so it stays sharp on high-DPI screens.
        // i18n-ignore: the options of the QR code
        twoFactor.qrDataUrl = await toDataURL(data.qr_uri, {
          width: 384,
          margin: 2,
          errorCorrectionLevel: "M"
        });
      } catch (e) {
        // The secret is still shown for manual entry.
        console.error("Failed to render QR code:", e);
        twoFactor.qrDataUrl = "";
      }
      twoFactor.setupOpen = true;
    } catch (e: any) {
      twoFactor.error = e.message || $t("account.totp.setupFailed");
    } finally {
      twoFactor.loading = false;
    }
  }
</script>

<!-- Two-Factor Authentication -->
<div class="card">
  <h3 class="card-title mb-4">
    <Smartphone class="w-5 h-5 text-content-secondary" />
    {$t("account.totp.title")}
  </h3>

  {#if twoFactor.success}
    <Notice variant="success" message={twoFactor.success} />
  {/if}

  {#if twoFactor.error && !twoFactor.setupOpen && !twoFactor.disableOpen}
    <Notice variant="error" message={twoFactor.error} />
  {/if}

  <div class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <p class="font-medium">{$t("account.totp.app")}</p>
      <p class="text-sm text-content-secondary">
        {$t("account.totp.appHint")}
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-3">
      {#if !twoFactor.statusLoaded}
        {#if !twoFactor.statusError}
          <span aria-busy="true"><Skeleton shape="block" class="h-7 w-28" /></span>
        {/if}
      {:else if twoFactor.enabled}
        <span class="px-3 py-1 bg-status-success/20 text-status-success rounded-lg text-sm flex items-center gap-1">
          <CheckCircle class="w-4 h-4" />
          {$t("account.totp.on")}
        </span>
        <Button variant="danger" onclick={openDisable}>
          {$t("account.totp.disable")}
        </Button>
      {:else}
        <span class="px-3 py-1 bg-surface-overlay text-content-secondary rounded-lg text-sm">
          {$t("account.totp.off")}
        </span>
        <Button variant="primary" onclick={startSetup} disabled={twoFactor.loading}>
          {twoFactor.loading ? $t("account.totp.settingUp") : $t("account.totp.enable")}
        </Button>
      {/if}
    </div>
  </div>

  {#if twoFactor.statusError}
    <Alert variant="error" title={$t("account.totp.statusError")} class="mt-4">
      <p>{twoFactor.statusError}</p>
      <Button variant="secondary" size="sm" class="mt-3" loading={twoFactor.statusLoading} onclick={() => twoFactor.loadStatus()}
        >{$t("common.retry")}</Button
      >
    </Alert>
  {/if}

  <!-- Turning 2FA off removes the codes and the security keys, and a new setup starts without
       any (ADR-0238, ADR-0409). -->
  {#if twoFactor.statusLoaded && twoFactor.enabled}
    <RecoveryCodes />
    <SecurityKeys />
  {:else if twoFactor.statusLoaded}
    <p class="mt-4 pt-4 border-t border-border text-sm text-content-secondary">{$t("securitykeys.needsTotp")}</p>
  {/if}
</div>
