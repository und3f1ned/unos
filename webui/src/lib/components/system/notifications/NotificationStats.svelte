<script lang="ts">
  import { AlertTriangle, Bell, CheckCircle, Send } from "lucide-svelte";
  import { Card, type IconComponent } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  // The counts above the views: the channels on, and the notifications sent, delivered and failed.
  type Props = {
    active: number;
    sent: number;
    delivered: number;
    failed: number;
  };

  let { active, sent, delivered, failed }: Props = $props();
</script>

{#snippet stat(Icon: IconComponent, iconClass: string, value: number, label: string)}
  <Card>
    <div class="flex items-center gap-3">
      <Icon class="w-5 h-5 {iconClass}" />
      <div>
        <p class="text-2xl font-bold">{value}</p>
        <p class="text-xs text-muted">{label}</p>
      </div>
    </div>
  </Card>
{/snippet}

<div class="grid grid-cols-2 md:grid-cols-4 gap-4">
  {@render stat(Bell, "text-info", active, $t("notifications.stats.active"))}
  {@render stat(Send, "text-info", sent, $t("notifications.stats.sent"))}
  {@render stat(CheckCircle, "text-success", delivered, $t("notifications.delivered"))}
  {@render stat(AlertTriangle, "text-error", failed, $t("notifications.failed"))}
</div>
