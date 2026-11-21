<script lang="ts">
  import { Button, Input } from '$lib/components/ui';
  import { AuthCard, FormError } from '$lib/components/auth';
  import { goto } from '$app/navigation';
  import { setup, login, USERNAME_PATTERN } from '$lib/api/auth';
  import { connectWebSocket } from '$lib/stores/stats';
  import { MIN_PASSWORD_LENGTH } from '$lib/components/users/password';
  import { t } from '$lib/i18n';


  let username = $state('');
  let password = $state('');
  let confirmPassword = $state('');
  let error = $state('');
  let loading = $state(false);
  // Create was pressed: every field says what is wrong with it, in the page's language, under the
  // field, instead of the browser's own bubble.
  let attempted = $state(false);

  const usernameError = $derived(
    !attempted ? '' : !username ? $t('setup.usernameRequired') : USERNAME_PATTERN.test(username) ? '' : $t('setup.usernameInvalid')
  );
  const passwordError = $derived(
    attempted && password.length < MIN_PASSWORD_LENGTH ? $t('setup.passwordShort', { n: MIN_PASSWORD_LENGTH }) : ''
  );
  const confirmError = $derived(
    attempted && confirmPassword !== password ? $t('setup.mismatch') : ''
  );

  async function handleSubmit() {
    attempted = true;
    if (usernameError || passwordError || confirmError) return;

    error = '';
    loading = true;

    try {
      await setup(username, password);
      await login(username, password);
      connectWebSocket();
      goto('/');
    } catch (e) {
      error = (e as Error).message || $t('setup.failed');
    } finally {
      loading = false;
    }
  }
</script>

<AuthCard intro={$t('setup.intro')}>
  <form
    novalidate
    onsubmit={(event) => {
      event.preventDefault();
      handleSubmit();
    }}
    class="space-y-4"
  >
    {#if error}
      <FormError message={error} />
    {/if}

    <Input
      id="username"
      label={$t('common.username')}
      bind:value={username}
      autocomplete="username"
      maxlength={32}
      hint={$t('setup.usernameHint')}
      error={usernameError}
    />
    <Input
      id="password"
      type="password"
      label={$t('common.password')}
      bind:value={password}
      autocomplete="new-password"
      hint={$t('setup.passwordHint', { n: MIN_PASSWORD_LENGTH })}
      error={passwordError}
    />
    <Input
      id="confirmPassword"
      type="password"
      label={$t('setup.confirmPassword')}
      bind:value={confirmPassword}
      autocomplete="new-password"
      error={confirmError}
    />

    <Button variant="primary" class="w-full" type="submit" loading={loading}>
      {$t('setup.create')}
    </Button>
  </form>
</AuthCard>
