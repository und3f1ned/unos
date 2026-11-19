<script lang="ts">
  import { Button } from '$lib/components/ui';
  import { AuthCard, FormError } from '$lib/components/auth';
  import { goto } from '$app/navigation';
  import { setup, login } from '$lib/api/auth';
  import { connectWebSocket } from '$lib/stores/stats';
  import { t } from '$lib/i18n';

  let username = $state('');
  let password = $state('');
  let confirmPassword = $state('');
  let error = $state('');
  let loading = $state(false);

  async function handleSubmit() {
    if (password !== confirmPassword) {
      error = $t('setup.mismatch');
      return;
    }

    error = '';
    loading = true;

    try {
      await setup(username, password);
      await login(username, password);
      connectWebSocket();
      goto('/');
    } catch (e: any) {
      error = e.message || $t('setup.failed');
    } finally {
      loading = false;
    }
  }
</script>

<AuthCard intro={$t('setup.intro')}>
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

    <div>
      <label for="username" class="block text-sm font-medium text-content-secondary mb-1">
        {$t('common.username')}
      </label>
      <input
        type="text"
        id="username"
        bind:value={username}
        class="form-input"
        required
      />
    </div>

    <div>
      <label for="password" class="block text-sm font-medium text-content-secondary mb-1">
        {$t('common.password')}
      </label>
      <input
        type="password"
        id="password"
        bind:value={password}
        class="form-input"
        required
        minlength="8"
      />
    </div>

    <div>
      <label for="confirmPassword" class="block text-sm font-medium text-content-secondary mb-1">
        {$t('setup.confirmPassword')}
      </label>
      <input
        type="password"
        id="confirmPassword"
        bind:value={confirmPassword}
        class="form-input"
        required
      />
    </div>

    <Button variant="primary" class="w-full" type="submit" disabled={loading}>
      {loading ? $t('setup.creating') : $t('setup.create')}
    </Button>
  </form>
</AuthCard>
