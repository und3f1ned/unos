<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { Clock, History, MapPin, Monitor, RefreshCw, User } from "lucide-svelte";
  import { getLoginHistory, type LoginAttempt } from "$lib/api/security";
  import { Alert, Badge, Button } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";

  // The account's own sign-ins (mine), an administrator's too: every account's are in the Audit Log,
  // which holds every failed sign-in and second factor (ADR-0430).

  // The sign-ins, newest first: loaded when the list is first shown.
  let loginHistory = $state<LoginAttempt[]>([]);
  let loginHistoryTotal = $state(0);
  let loginHistoryFailed = $state(0);
  let loginHistorySuccess = $state(0);
  let loginHistoryLoading = $state(false);
  // Why the last load failed: shown in place of the counts and the list.
  let loginHistoryError = $state("");
  let showLoginHistory = $state(false);

  async function loadLoginHistory() {
    loginHistoryLoading = true;
    loginHistoryError = "";
    try {
      const response = await getLoginHistory(50, 0, undefined, undefined, true);
      loginHistory = response.attempts;
      loginHistoryTotal = response.total;
      loginHistoryFailed = response.failed_count;
      loginHistorySuccess = response.success_count;
    } catch (e) {
      // Not an empty history: what the list and the counts would be is unknown.
      loginHistory = [];
      loginHistoryError = messageOf(e);
    } finally {
      loginHistoryLoading = false;
    }
  }

  async function toggleLoginHistory() {
    showLoginHistory = !showLoginHistory;
    if (showLoginHistory && loginHistory.length === 0) {
      await loadLoginHistory();
    }
  }

  // Reactive, so that an open list follows a change of the language. The times are in the
  // language of the page, in the browser's own format when it is of that language.
  let formatTimestamp = $derived((ts: string | null): string => {
    if (!ts) return $t("common.unknown");
    return formatDateTime(ts, $intlLocale);
  });

  // i18n-ignore: the names of browsers, the same in every language
  const BROWSERS = ["Chrome", "Firefox", "Safari", "Edge"];

  // The first of BROWSERS that the user agent names: Chrome's also names Safari.
  let parseUserAgent = $derived((ua: string | null): string => {
    if (!ua) return $t("common.unknown");
    return BROWSERS.find((name) => ua.includes(name)) ?? $t("account.history.browser");
  });
</script>

<!-- Login History -->
<div class="card">
  <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
    <h3 class="card-title">
      <History class="w-5 h-5 text-content-secondary" />
      {$t("account.history.title")}
    </h3>
    <Button variant="primary" onclick={toggleLoginHistory}>
      {showLoginHistory ? $t("account.history.hide") : $t("account.history.show")}
    </Button>
  </div>

  <p class="text-sm text-content-secondary">
    {$t("account.history.description")}
  </p>

  {#if showLoginHistory}
    <div class="mt-4 space-y-4">
      {#if loginHistoryLoading}
        <div class="text-center py-4 text-content-secondary">{$t("common.loading")}</div>
      {:else if loginHistoryError}
        <Alert variant="error" title={$t("account.history.loadError")}>
          <p>{loginHistoryError}</p>
          <Button variant="secondary" size="sm" class="mt-3" onclick={loadLoginHistory}>{$t("common.retry")}</Button>
        </Alert>
      {:else}
        <!-- Stats -->
        <div class="grid grid-cols-3 gap-4">
          <div class="p-3 bg-surface-overlay rounded-lg text-center">
            <p class="text-2xl font-bold">{loginHistoryTotal}</p>
            <p class="text-sm text-content-secondary">{$t("account.history.total")}</p>
          </div>
          <div class="p-3 bg-surface-overlay rounded-lg text-center">
            <p class="text-2xl font-bold text-status-success">{loginHistorySuccess}</p>
            <p class="text-sm text-content-secondary">{$t("account.history.successful")}</p>
          </div>
          <div class="p-3 bg-surface-overlay rounded-lg text-center">
            <p class="text-2xl font-bold text-status-error">{loginHistoryFailed}</p>
            <p class="text-sm text-content-secondary">{$t("account.history.failed")}</p>
          </div>
        </div>

        <!-- History List -->
        {#if loginHistory.length === 0}
          <p class="text-sm text-content-secondary text-center py-4">{$t("account.history.empty")}</p>
        {:else}
          <!-- Newest first; the times are the browser's local time (ADR-0262). -->
          <ul class="space-y-2 max-h-80 overflow-y-auto" aria-label={$t("account.history.list")}>
            {#each loginHistory as attempt (attempt.id)}
              <li class="flex items-center gap-3 p-3 bg-surface-overlay rounded-lg">
                <Badge variant={attempt.success ? "success" : "error"}>
                  {attempt.success ? $t("account.history.success") : $t("account.history.failure")}
                </Badge>
                <!-- On a phone the details wrap, and a long name or IPv6 address breaks (ADR-0302). -->
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <User class="w-4 h-4 text-content-secondary flex-shrink-0" />
                    <span class="font-medium break-all">{attempt.username}</span>
                  </div>
                  <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-content-secondary">
                    <span class="flex items-center gap-1">
                      <Clock class="w-3 h-3" />
                      <time datetime={attempt.timestamp ?? undefined}>{formatTimestamp(attempt.timestamp)}</time>
                    </span>
                    {#if attempt.ip_address}
                      <span class="flex items-center gap-1 min-w-0 break-all">
                        <MapPin class="w-3 h-3 flex-shrink-0" />
                        {attempt.ip_address}
                      </span>
                    {/if}
                    {#if attempt.user_agent}
                      <span class="flex items-center gap-1">
                        <Monitor class="w-3 h-3" />
                        {parseUserAgent(attempt.user_agent)}
                      </span>
                    {/if}
                  </div>
                  {#if attempt.failure_reason}
                    <p class="text-xs text-status-error mt-1">{attempt.failure_reason}</p>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
          <Button variant="secondary" class="w-full" onclick={loadLoginHistory}>
            <RefreshCw class="w-4 h-4" />
            {$t("common.refresh")}
          </Button>
        {/if}
      {/if}
    </div>
  {/if}
</div>
