<script lang="ts">
  import { onMount } from "svelte";
  import {
    ApiTokensCard,
    LoginHistoryCard,
    PasswordCard,
    TwoFactor,
    TwoFactorCard,
    TwoFactorDialogs
  } from "$lib/components/account";
  import { t } from "$lib/i18n";

  // The Account page's Sign-in and security (ADR-0430): the account's own password, two-factor
  // authentication with its recovery codes and security keys, API tokens and sign-in history.
  // The two-factor status and its setup are shared by the card and its dialogs, which the tab
  // draws after its content.
  const twoFactor = new TwoFactor();

  onMount(() => {
    void twoFactor.loadStatus();
  });
</script>

<div class="space-y-4">
  <h2 class="text-xl font-semibold">{$t("account.tabs.security")}</h2>

  <PasswordCard />

  <TwoFactorCard {twoFactor} />

  <ApiTokensCard />

  <!-- The account's own attempts, also an administrator's: every account's are in the Audit Log. -->
  <LoginHistoryCard />
</div>

<!-- The dialogs take Modal's overlay (ADR-0302): one taller than the window scrolls with it, so
     that its title and buttons stay reachable; my-auto centers it while it fits. -->
<TwoFactorDialogs {twoFactor} />
