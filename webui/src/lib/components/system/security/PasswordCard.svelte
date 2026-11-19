<script lang="ts">
  import { Key } from "lucide-svelte";
  import { changePassword } from "$lib/api/auth";
  import { Alert, Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import Notice from "./Notice.svelte";
  import RevealablePassword from "./RevealablePassword.svelte";

  // The account's own password: the current one and a new one, typed twice.
  let currentPassword = $state("");
  let newPassword = $state("");
  let confirmPassword = $state("");
  let passwordError = $state("");
  let passwordSuccess = $state("");
  // E.g. open SMB connections that could not be ended: shown until dismissed.
  let passwordWarnings = $state<string[]>([]);
  let changingPassword = $state(false);

  async function handlePasswordChange() {
    passwordError = "";
    passwordSuccess = "";
    passwordWarnings = [];

    if (newPassword !== confirmPassword) {
      passwordError = $t("account.password.mismatch");
      return;
    }

    if (newPassword.length < 8) {
      passwordError = $t("account.password.tooShort");
      return;
    }

    changingPassword = true;
    try {
      const result = await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      passwordSuccess = $t("account.password.changed");
      passwordWarnings = result?.warnings ?? [];
      currentPassword = "";
      newPassword = "";
      confirmPassword = "";
    } catch (e: any) {
      passwordError = e.message || $t("account.password.failed");
    } finally {
      changingPassword = false;
    }
  }
</script>

<!-- Change Password -->
<div class="card">
  <h3 class="card-title mb-4">
    <Key class="w-5 h-5 text-content-secondary" />
    {$t("account.password.title")}
  </h3>

  {#if passwordSuccess}
    <Notice variant="success" message={passwordSuccess} />
  {/if}

  {#if passwordWarnings.length > 0}
    <Alert variant="warning" class="mb-4" dismissible ondismiss={() => (passwordWarnings = [])}>
      {#each passwordWarnings as warning}
        <p>{warning}</p>
      {/each}
    </Alert>
  {/if}

  {#if passwordError}
    <Notice variant="error" message={passwordError} />
  {/if}

  <form onsubmit={(e) => { e.preventDefault(); handlePasswordChange(); }} class="space-y-4 max-w-md">
    <div>
      <label for="currentPassword" class="block text-sm font-medium text-content-secondary mb-1">
        {$t("account.password.current")}
      </label>
      <RevealablePassword id="currentPassword" bind:value={currentPassword} />
    </div>

    <div>
      <label for="newPassword" class="block text-sm font-medium text-content-secondary mb-1">
        {$t("account.password.new")}
      </label>
      <RevealablePassword id="newPassword" bind:value={newPassword} minlength={8} />
      <p class="text-xs text-content-muted mt-1">{$t("account.password.minimum")}</p>
    </div>

    <div>
      <label for="confirmPassword" class="block text-sm font-medium text-content-secondary mb-1">
        {$t("account.password.confirm")}
      </label>
      <input
        type="password"
        id="confirmPassword"
        bind:value={confirmPassword}
        class="form-input"
        required
      />
    </div>

    <Button variant="primary" type="submit" disabled={changingPassword}>
      {changingPassword ? $t("account.password.changing") : $t("account.password.submit")}
    </Button>
  </form>
</div>
