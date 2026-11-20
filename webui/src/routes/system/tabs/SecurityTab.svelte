<script lang="ts">
  import { onMount } from "svelte";
  import { user } from "$lib/stores/auth";
  import SmbAccessCard from "$lib/components/account/SmbAccessCard.svelte";
  import TwoFactorRequirementCard from "$lib/components/account/TwoFactorRequirementCard.svelte";
  import FirewallCard from "$lib/components/account/FirewallCard.svelte";
  import LanguageCard from "$lib/components/account/LanguageCard.svelte";
  import ThemeCard from "$lib/components/account/ThemeCard.svelte";
  import ApiTokensCard from "$lib/components/account/ApiTokensCard.svelte";
  import {
    Certificates,
    CertificatesCard,
    GenerateCertificateDialog,
    LoginHistoryCard,
    PasswordCard,
    ServeCertificateDialog,
    TwoFactor,
    TwoFactorCard,
    TwoFactorDialogs
  } from "$lib/components/system/security";
  import { t } from "$lib/i18n";

  // The two-factor status and its setup, and the certificates: each shared by a card and its
  // dialogs, which the page draws after its content.
  const twoFactor = new TwoFactor();
  const certificates = new Certificates();

  // Turning SMB on needs the two-factor code exactly while 2FA is on: the
  // SMB card reloads its status when 2FA changes here.
  let smbCard: SmbAccessCard | undefined;

  // The firewall and the certificates are administrator-only APIs; every account can
  // manage its own password, 2FA and see its own login history.
  let isAdmin = $derived($user?.is_admin === true);

  onMount(async () => {
    await Promise.all([
      twoFactor.loadStatus(),
      ...(isAdmin ? [certificates.load()] : [])
    ]);
  });
</script>

<!-- Two runs of cards under divider labels that are no headings (ADR-0428): the signed-in user's
     own settings, then, for an administrator, the NAS's. -->
{#snippet divider(label: string)}
  <div class="flex items-center gap-3 pt-2">
    <p class="eyebrow shrink-0">{label}</p>
    <span class="h-px flex-1 bg-border" aria-hidden="true"></span>
  </div>
{/snippet}

<div class="space-y-4">
  <h2 class="text-xl font-semibold">{$t("account.title")}</h2>

  {#if isAdmin}
    {@render divider($t("account.group.mine"))}
  {/if}

  <LanguageCard />

  <ThemeCard />

  <TwoFactorCard {twoFactor} />

  <PasswordCard />

  <SmbAccessCard bind:this={smbCard} />

  <ApiTokensCard />

  <LoginHistoryCard />

  {#if isAdmin}
    {@render divider($t("account.group.nas"))}

    {#if twoFactor.statusLoaded}
      <TwoFactorRequirementCard ownTwoFactor={twoFactor.enabled} />
    {/if}

    <FirewallCard />

    <CertificatesCard {certificates} />
  {/if}
</div>

<!-- The dialogs below take Modal's overlay (ADR-0302): one taller than the window scrolls with
     it, so that its title and buttons stay reachable; my-auto centers it while it fits. -->

<TwoFactorDialogs {twoFactor} onchange={() => void smbCard?.refresh()} />

<!-- Serve a stored certificate, or Caddy's local CA again: every browser sees a new certificate (ADR-0404). -->
<ServeCertificateDialog {certificates} />

<GenerateCertificateDialog {certificates} />
