<script lang="ts">
  import { RotateCcw } from "lucide-svelte";
  import { updateRuns, type UpdateProgress } from "$lib/api/updates";
  import { Alert, Card, CardAction, ProgressBar, StatusDot } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  // The update or rollback of UNOS that runs, or the last one, and Rollback (ADR-0439): what the
  // events socket sends at each step, or GET /updates/progress at the tab's load.
  type Props = {
    progress: UpdateProgress;
    rollingBack?: boolean;
    onrollback: () => void;
  };

  let { progress, rollingBack = false, onrollback }: Props = $props();

  let runs = $derived(updateRuns(progress));
  let version = $derived(progress.version ?? "");

  // The step that runs, in the language of the page (the server's message stays English).
  const STEPS: Record<string, string> = {
    checking: "updates.job.checking",
    downloading: "updates.job.downloading",
    backing_up: "updates.job.backingUp",
    installing: "updates.job.installing",
    verifying: "updates.job.verifying",
    rollback: "updates.job.rollback",
  };
  let ended = $derived(
    progress.status === "completed"
      ? progress.action === "rollback"
        ? $t("updates.job.rolledBack", { version })
        : $t("updates.job.updated", { version })
      : progress.action === "rollback"
        ? version
          ? $t("updates.job.rollbackFailed", { version })
          : $t("updates.job.rollbackFailedEarly")
        : $t("updates.job.updateFailed", { version }),
  );
</script>

<Card title={$t("updates.job.title")}>
  {#snippet actions()}
    {#if progress.can_rollback && !runs}
      <CardAction icon={RotateCcw} loading={rollingBack} onclick={onrollback}>{$t("updates.rollback")}</CardAction>
    {/if}
  {/snippet}
  <div class="space-y-3 text-sm" data-testid="update-job">
    {#if runs}
      <p class="flex items-center gap-2 text-content">
        <StatusDot status="info" pulse label={$t("updates.job.running")} />
        <span>{$t(STEPS[progress.status] ?? "updates.job.checking", { version })}</span>
      </p>
      <ProgressBar value={progress.progress_percent} variant="info" size="sm" label={$t("updates.job.progress")} />
    {:else if progress.status === "completed"}
      <p class="flex items-center gap-2 text-content">
        <StatusDot status="success" label={$t("updates.job.done")} />
        <span>{ended}</span>
      </p>
    {:else if progress.status === "failed"}
      <div role="alert">
        <Alert variant="error" size="sm" title={ended}>
          {#if progress.rolled_back}{$t("updates.job.rolledBackAfter")}{/if}
          {#snippet details()}<pre class="whitespace-pre-wrap break-words">{progress.error ?? ""}</pre>{/snippet}
        </Alert>
      </div>
    {/if}
  </div>
</Card>
