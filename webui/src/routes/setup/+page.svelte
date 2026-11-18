<script lang="ts">
  import { Button } from '$lib/components/ui';
  import { goto } from '$app/navigation';
  import { setup, login } from '$lib/api/auth';
  import { connectWebSocket } from '$lib/stores/stats';
  import { Server } from 'lucide-svelte';
  import { t } from '$lib/i18n';

  let username = '';
  let password = '';
  let confirmPassword = '';
  let error = '';
  let loading = false;

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

<div class="min-h-screen flex items-center justify-center bg-surface">
  <div class="bg-surface-elevated p-8 rounded-lg shadow-xl w-full max-w-md">
    <div class="flex items-center justify-center gap-2 mb-4">
      <Server class="w-10 h-10 text-brand-ring" />
      <span class="text-2xl font-bold">{$t('common.product')}</span>
    </div>

    <p class="text-center text-content-secondary mb-8">
      {$t('setup.intro')}
    </p>

    <form on:submit|preventDefault={handleSubmit} class="space-y-4">
      {#if error}
        <div class="bg-status-error/20 border border-status-error text-status-error px-4 py-2 rounded">
          {error}
        </div>
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

      <Button variant="primary" class="w-full"
        type="submit"
        disabled={loading}
       
      >
        {loading ? $t('setup.creating') : $t('setup.create')}
      </Button>
    </form>
  </div>
</div>
