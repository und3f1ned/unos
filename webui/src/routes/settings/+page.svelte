<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { t } from '$lib/i18n';

  onMount(() => {
    goto('/system#general');
  });
</script>

<div class="flex items-center justify-center h-64">
  <p class="text-slate-500">{$t('general.redirecting')}</p>
</div>
