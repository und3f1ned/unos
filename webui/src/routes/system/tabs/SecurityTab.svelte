<script lang="ts">
  import { onMount } from "svelte";
  import { Alert, Button } from "$lib/components/ui";
  import { TwoFactor } from "$lib/components/account";
  import {
    Certificates,
    CertificatesCard,
    FirewallCard,
    GenerateCertificateDialog,
    TwoFactorRequirementCard
  } from "$lib/components/system/security";
  import { t } from "$lib/i18n";

  // System > Security (ADR-0431): what applies to the whole NAS, for administrators: the
  // two-factor requirement of every account, the host's firewall and the certificate that HTTPS
  // serves. The administrator's own sign-in settings are on the Account page, every account's
  // sign-ins in the Audit Log.

  // Turning the requirement on or off takes a code of the administrator's own authenticator app:
  // the card needs to know whether the account has one.
  const ownTwoFactor = new TwoFactor();
  // The certificates, shared by the card and its dialogs, which the tab draws after its content.
  const certificates = new Certificates();

  onMount(() => {
    void ownTwoFactor.loadStatus();
    void certificates.load();
  });
</script>

<div class="space-y-4">
  <h2 class="text-xl font-semibold">{$t("system.security.title")}</h2>

  {#if ownTwoFactor.statusLoaded}
    <TwoFactorRequirementCard ownTwoFactor={ownTwoFactor.enabled} />
  {:else if ownTwoFactor.statusError}
    <Alert variant="error" title={$t("account.totp.statusError")}>
      <p>{ownTwoFactor.statusError}</p>
      <Button
        variant="secondary"
        size="sm"
        class="mt-3"
        loading={ownTwoFactor.statusLoading}
        onclick={() => ownTwoFactor.loadStatus()}>{$t("common.retry")}</Button
      >
    </Alert>
  {/if}

  <FirewallCard />

  <CertificatesCard {certificates} />
</div>

<GenerateCertificateDialog {certificates} />
