<script lang="ts">
  import { FileKey, Plus, Trash2 } from "lucide-svelte";
  import { messageOf } from "$lib/errors";
  import { deleteCertificate, serveCertificate, LOCAL_CA_ID, type Certificate } from "$lib/api/security";
  import { Alert, Badge, Button, CardAction, EmptyState, Skeleton, confirm, toast } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { CERTIFICATE_TYPES, SERVED_BADGES, SERVED_BY, storedCertificate } from "./certificates";
  import type { Certificates } from "./certificates.svelte";

  // The web server's certificates (ADR-0404): what browsers get, and under Manage the stored
  // certificates, which can be served, generated or deleted. Serving and deleting ask with confirm();
  // generating is GenerateCertificateDialog.
  type Props = {
    certificates: Certificates;
  };

  let { certificates }: Props = $props();

  let showCertModal = $state(false);

  /** The label of `value` in the language of the page; a value without one as it comes. */
  let labelOf = $derived((labels: Record<string, string>, value: string): string =>
    Object.prototype.hasOwnProperty.call(labels, value) ? $t(labels[value]) : value);

  /** A certificate's date (UTC with its offset, ADR-0401) in the language of the page and the browser's time zone. */
  let formatCertificateDate = $derived((value: string): string => formatDateTime(value, $intlLocale));

  // What browsers get, in words: the served certificate's name, or what serves theirs.
  let servedCert = $derived(storedCertificate(certificates.list, certificates.status?.served_id));
  let chosenCert = $derived(storedCertificate(certificates.list, certificates.status?.chosen_id));
  let servedSource = $derived(
    !certificates.status
      ? ""
      : certificates.status.served_by === "stored_certificate" && servedCert
        ? servedCert.domain
        : labelOf(SERVED_BY, certificates.status.served_by)
  );
  // A stored certificate was chosen, and the web server answered with another.
  let chosenNotServed = $derived(
    !!certificates.status?.chosen_id &&
    certificates.status.served_by !== "unknown" &&
    certificates.status.served_id !== certificates.status.chosen_id
  );

  /**
   * Serve a stored certificate, or Caddy's local CA again, once confirmed: every browser sees a
   * new certificate (ADR-0404).
   */
  async function askServe(cert: Certificate | null) {
    const yes = await confirm({
      title: cert ? $t("account.certs.serveTitle") : $t("account.certs.localCaTitle"),
      message: cert ? $t("account.certs.serveText", { domain: cert.domain }) : $t("account.certs.localCaText"),
      warning: $t("account.certs.newCertificateWarning"),
      consequences:
        cert?.expires_soon && cert.valid_until
          ? [$t("account.certs.expiresSoonWarning", { date: formatCertificateDate(cert.valid_until) })]
          : [],
      confirmLabel: cert ? $t("account.certs.serveConfirm") : $t("account.certs.useLocalCa"),
      tone: "warning"
    });
    if (!yes) return;
    certificates.error = "";
    try {
      const answer = await serveCertificate(cert ? cert.id : LOCAL_CA_ID);
      certificates.status = answer.status;
      toast.success(cert ? $t("account.certs.nowServed", { domain: cert.domain }) : $t("account.certs.nowLocalCa"));
      await certificates.load();
    } catch (e) {
      // The server's refusal stays on the card, by the certificates it concerns.
      certificates.error = messageOf(e);
    }
  }

  async function handleDeleteCert(cert: Certificate) {
    const yes = await confirm({
      title: $t("account.certs.deleteTitle", { domain: cert.domain }),
      message: cert.chosen ? $t("account.certs.confirmDeleteServed") : undefined,
      confirmLabel: $t("account.certs.deleteAction"),
    });
    if (!yes) return;

    certificates.loading = true;
    certificates.error = "";
    try {
      await deleteCertificate(cert.id);
      toast.success($t("account.certs.deleted"));
      await certificates.load();
    } catch (e: any) {
      certificates.error = e.message || $t("account.certs.deleteFailed");
    } finally {
      certificates.loading = false;
    }
  }
</script>

<!-- SSL/TLS Certificates -->
<div class="card">
  <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
    <h3 class="card-title">
      <FileKey class="w-5 h-5 text-content-secondary" />
      {$t("account.certs.title")}
    </h3>
    <div class="flex flex-wrap items-center gap-2">
      {#if certificates.status}
        <Badge variant={SERVED_BADGES[certificates.status.served_by] ?? "info"}>{servedSource}</Badge>
      {:else if !certificates.loadError}
        <!-- The badge's place until the status comes (ADR-0430). -->
        <span aria-busy="true"><Skeleton shape="block" class="h-6 w-28 rounded-full" /></span>
      {/if}
      <CardAction aria-expanded={showCertModal} onclick={() => (showCertModal = !showCertModal)}>
        {$t("account.certs.manage")}
      </CardAction>
    </div>
  </div>

  {#if certificates.error}
    <Alert variant="error" class="mb-4">{certificates.error}</Alert>
  {/if}

  {#if certificates.loadError}
    <Alert variant="error" class="mb-4">
      <p>{certificates.loadError}</p>
      <Button variant="secondary" size="sm" class="mt-3" loading={certificates.loading} onclick={() => certificates.load()}
        >{$t("common.retry")}</Button
      >
    </Alert>
  {/if}

  <p class="text-sm text-content-secondary">
    {$t("account.certs.description")}
  </p>

  {#if showCertModal}
    <div class="mt-4 space-y-4">
      {#if certificates.status}
        <div class="p-3 bg-surface-muted rounded-lg min-w-0 break-words">
          <p class="font-medium">
            {$t("account.certs.servedBy", { source: servedSource })}
          </p>
          <p class="text-sm text-content-secondary">{$t("account.certs.servedHint")}</p>
          {#if certificates.status.served_by === "unknown"}
            <p class="text-sm text-status-warning mt-2">{$t("account.certs.servedUnknownHint")}</p>
          {/if}
          {#if chosenNotServed}
            <Alert variant="warning" class="mt-3">
              {$t("account.certs.chosenNotServed", {
                domain: chosenCert?.domain ?? certificates.status.chosen_id ?? "",
                source: servedSource
              })}
            </Alert>
          {/if}
          {#if certificates.status.chosen_id || certificates.status.served_by === "stored_certificate"}
            <Button variant="secondary" size="sm" class="mt-3" onclick={() => askServe(null)}>
              {$t("account.certs.useLocalCa")}
            </Button>
          {/if}
        </div>
      {/if}

      <div class="flex flex-wrap justify-between items-center gap-2">
        <h4 class="font-medium">{$t("account.certs.stored")}</h4>
        <Button variant="secondary" size="sm" icon={Plus} onclick={() => certificates.openGenerate()}>
          {$t("account.certs.generateSelfSigned")}
        </Button>
      </div>

      {#if certificates.list.length === 0}
        {#if certificates.loaded}
          <EmptyState compact icon={FileKey} title={$t("account.certs.noneFound")} />
        {/if}
      {:else}
        <div class="space-y-2">
          {#each certificates.list as cert}
            <div class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
              <div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                <Badge variant={cert.is_valid ? "success" : "error"}>
                  {cert.is_valid ? $t("account.certs.validBadge") : $t("account.certs.expiredBadge")}
                </Badge>
                {#if cert.served}
                  <Badge variant="success">{$t("account.certs.servedBadge")}</Badge>
                {:else if cert.chosen}
                  {#if certificates.status?.served_by !== "unknown"}
                    <Badge variant="warning">{$t("account.certs.notLoadedBadge")}</Badge>
                  {/if}
                {:else}
                  <Badge variant="neutral">{$t("account.certs.notServedBadge")}</Badge>
                {/if}
                {#if cert.expires_soon}
                  <Badge variant="warning">{$t("account.certs.expiresSoonBadge")}</Badge>
                {/if}
                <div class="min-w-0 break-words">
                  <p class="font-medium">{cert.domain}</p>
                  <p class="text-sm text-content-secondary">
                    {labelOf(CERTIFICATE_TYPES, cert.type)} |
                      {$t("account.certs.issuer", { issuer: cert.issuer || $t("account.certs.self") })}
                    {#if cert.valid_until}| {$t("account.certs.until", { date: formatCertificateDate(cert.valid_until) })}{/if}
                  </p>
                </div>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                {#if cert.is_valid && !cert.served}
                  <Button variant="secondary" size="sm" onclick={() => askServe(cert)}
                    aria-label={$t("account.certs.serveLabel", { domain: cert.domain })}
                  >
                    {$t("account.certs.serve")}
                  </Button>
                {/if}
                {#if cert.id.startsWith('custom_')}
                  <Button variant="ghost-danger" size="icon"
                    onclick={() => handleDeleteCert(cert)}
                    aria-label={$t("account.certs.delete", { domain: cert.domain })}
                  >
                    <Trash2 class="w-4 h-4" />
                  </Button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>
