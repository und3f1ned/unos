<script lang="ts">
  // The form of the off-device copy's target and of what it copies (ADR-0337, ADR-0422): saving
  // tests the target. The fields are those of `form`; the section saves, makes the key pair and
  // forgets the settings.
  import type { OffsiteSettingsAnswer, OffsiteStatus } from "$lib/api/offsite";
  import { Alert, Button, Checkbox, FieldGroup, Input, RadioGroup } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { Key, Plus, Trash2 } from "lucide-svelte";
  import type { OffsiteForm } from "./form.svelte";
  import { MIN_PASSWORD_LENGTH } from "./offsite";

  type Props = {
    form: OffsiteForm;
    answer: OffsiteSettingsAnswer;
    running: OffsiteStatus["running"];
    saving: boolean;
    canSave: boolean;
    makingKey: boolean;
    onsave: () => void;
    onmakekey: () => void;
    onforget: () => void;
  };

  let { form, answer, running, saving, canSave, makingKey, onsave, onmakekey, onforget }: Props = $props();

  // The server's bounds (supervisor/src/offsite/service.py): MAX_KEEP snapshots of a period, and
  // MAX_LIMIT_KIB KiB/s.
  const MAX_KEEP = 10_000;
  const MAX_LIMIT_KIB = 10_000_000;
</script>

<!-- The limits of a target that copies over the network: the same fields for every kind, a group
     of two with their unit (ADR-0432). -->
{#snippet limits()}
  <FieldGroup legend={$t("offsite.form.limits")} description={$t("offsite.form.limitHint")} minWidth="10rem" class="md:col-span-2">
    <Input id="offsite-limit-upload" type="number" label={$t("offsite.form.upload")} bind:value={form.limitUpload} suffix={$t("offsite.form.kibps")} min={0} max={MAX_LIMIT_KIB} boundsHint={false} width="sm" />
    <Input id="offsite-limit-download" type="number" label={$t("offsite.form.download")} bind:value={form.limitDownload} suffix={$t("offsite.form.kibps")} min={0} max={MAX_LIMIT_KIB} boundsHint={false} width="sm" />
  </FieldGroup>
{/snippet}

<form
  class="space-y-4"
  onsubmit={(e) => {
    e.preventDefault();
    onsave();
  }}
>
  <h4 class="font-medium text-sm">{$t("offsite.form.title")}</h4>
  <RadioGroup
    name="offsite-kind"
    legend={$t("offsite.form.kind")}
    legendHidden
    inline
    bind:value={form.kind}
    options={[
      { value: "disk", label: $t("offsite.form.disk") },
      { value: "s3", label: $t("offsite.form.s3") },
      { value: "rest", label: $t("offsite.form.rest") },
      { value: "sftp", label: $t("offsite.form.sftp") },
    ]}
  />

  {#if form.kind === "disk"}
    <Input id="offsite-path" label={$t("offsite.form.path")} bind:value={form.path} hint={$t("offsite.form.pathHint")} spellcheck={false} />
  {:else if form.kind === "rest"}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <Input id="offsite-rest-endpoint" label={$t("offsite.rest.endpoint")} bind:value={form.endpoint} hint={$t("offsite.rest.endpointHint")} spellcheck={false} />
      <Input id="offsite-rest-prefix" label={$t("offsite.rest.prefix")} bind:value={form.prefix} spellcheck={false} />
      <Input id="offsite-rest-user" label={$t("offsite.rest.username")} bind:value={form.username} autocomplete="off" spellcheck={false} />
      <Input
        id="offsite-rest-password"
        type="password"
        label={$t("offsite.rest.password")}
        bind:value={form.restPassword}
        autocomplete="new-password"
        hint={form.keepsRestPassword ? $t("offsite.rest.passwordKept") : ""}
      />
      <div>
        <label for="offsite-rest-tls" class="form-label">{$t("offsite.rest.tls")}</label>
        <select id="offsite-rest-tls" class="form-select" bind:value={form.tls}>
          <option value="system">{$t("offsite.rest.tlsSystem")}</option>
          <option value="fingerprint">{$t("offsite.rest.tlsFingerprint")}</option>
          <option value="ca">{$t("offsite.rest.tlsCa")}</option>
        </select>
      </div>
      {#if form.tls === "fingerprint"}
        <Input
          id="offsite-rest-fingerprint"
          label={$t("offsite.rest.fingerprint")}
          bind:value={form.certificateSha256}
          hint={$t("offsite.rest.fingerprintHint")}
          spellcheck={false}
        />
      {/if}
      {@render limits()}
    </div>
    {#if form.tls === "ca"}
      <div>
        <label for="offsite-rest-certificate" class="form-label">{$t("offsite.rest.certificate")}</label>
        <textarea
          id="offsite-rest-certificate"
          class="form-input font-mono text-xs"
          rows="6"
          spellcheck="false"
          bind:value={form.certificate}
          aria-describedby="offsite-rest-certificate-hint"
        ></textarea>
        <p id="offsite-rest-certificate-hint" class="form-hint">{$t("offsite.rest.certificateHint")}</p>
      </div>
    {/if}
  {:else if form.kind === "sftp"}
    <div class="card-nested p-3 space-y-2 text-sm">
      <h5 class="font-medium">{$t("offsite.sftp.keyTitle")}</h5>
      <p class="text-content-secondary">{$t("offsite.sftp.keyIntro")}</p>
      {#if answer.sftp_public_key}
        <p class="form-label">{$t("offsite.sftp.publicKey")}</p>
        <code class="block font-mono text-xs break-all select-all" data-testid="sftp-public-key">{answer.sftp_public_key}</code>
        <p class="form-hint">{$t("offsite.sftp.publicKeyHint")}</p>
      {:else}
        <p class="text-status-warning">{$t("offsite.sftp.noKey")}</p>
      {/if}
      <Button size="sm" variant="secondary" icon={Key} loading={makingKey} disabled={running !== null} onclick={onmakekey}>
        {answer.sftp_public_key ? $t("offsite.sftp.newKey") : $t("offsite.sftp.makeKey")}
      </Button>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <Input id="offsite-sftp-host" label={$t("offsite.sftp.host")} bind:value={form.sftpHost} spellcheck={false} />
      <Input id="offsite-sftp-port" type="number" label={$t("offsite.sftp.port")} bind:value={form.sftpPort} />
      <Input id="offsite-sftp-user" label={$t("offsite.sftp.username")} bind:value={form.username} autocomplete="off" spellcheck={false} />
      <Input id="offsite-sftp-path" label={$t("offsite.sftp.path")} bind:value={form.remotePath} hint={$t("offsite.sftp.pathHint")} spellcheck={false} />
      {@render limits()}
    </div>
    {#if form.pinShown}
      <div class="space-y-2 text-sm">
        <p class="break-all" data-testid="sftp-pinned">{$t("offsite.sftp.pinned", { fingerprint: answer.host_key_fingerprint ?? "" })}</p>
        <p class="form-hint">{$t("offsite.sftp.pinnedHint")}</p>
        <Checkbox bind:checked={form.trustNewHostKey} label={$t("offsite.sftp.trustNewKey")} />
      </div>
    {/if}
    {#if form.fingerprintShown}
      <Input
        id="offsite-sftp-fingerprint"
        label={form.pinShown ? $t("offsite.sftp.newFingerprint") : $t("offsite.sftp.fingerprint")}
        bind:value={form.hostKeyFingerprint}
        hint={$t("offsite.sftp.fingerprintHint")}
        spellcheck={false}
      />
    {/if}
  {:else}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <Input id="offsite-endpoint" label={$t("offsite.form.endpoint")} bind:value={form.endpoint} hint={$t("offsite.form.endpointHint")} spellcheck={false} />
      <Input id="offsite-bucket" label={$t("offsite.form.bucket")} bind:value={form.bucket} spellcheck={false} />
      <Input id="offsite-prefix" label={$t("offsite.form.prefix")} bind:value={form.prefix} spellcheck={false} />
      <Input id="offsite-region" label={$t("offsite.form.region")} bind:value={form.region} spellcheck={false} />
      <Input id="offsite-access-key" label={$t("offsite.form.accessKeyId")} bind:value={form.accessKeyId} autocomplete="off" spellcheck={false} />
      <Input
        id="offsite-secret-key"
        type="password"
        label={$t("offsite.form.secretKey")}
        bind:value={form.secretKey}
        autocomplete="new-password"
        hint={form.keepsSecret ? $t("offsite.form.secretKept") : ""}
      />
      {@render limits()}
    </div>
  {/if}

  <fieldset class="space-y-2 text-sm">
    <legend class="form-label">{$t("offsite.form.sources")}</legend>
    {#each answer.pools as pool (pool.path)}
      <Checkbox value={pool.path} bind:group={form.poolSources} class="break-all">
        <span class="font-mono text-xs">{pool.path}</span>
      </Checkbox>
    {/each}
    {#each form.extraSources as source}
      <div class="flex items-center gap-2">
        <span class="font-mono text-xs break-all flex-1 min-w-0">{source}</span>
        <Button size="icon-sm" variant="ghost" icon={Trash2} onclick={() => form.removeSource(source)}>
          <span class="sr-only">{$t("offsite.form.removeSource", { path: source })}</span>
        </Button>
      </div>
    {/each}
    <div class="flex flex-wrap items-end gap-2">
      <div class="min-w-0 flex-1 basis-56">
        <Input id="offsite-new-source" label={$t("offsite.form.newSource")} bind:value={form.newSource} spellcheck={false} />
      </div>
      <Button variant="secondary" icon={Plus} disabled={!form.newSource.trim()} onclick={() => form.addSource()}>{$t("offsite.form.addSource")}</Button>
    </div>
    <p class="form-hint">{form.sources.length ? $t("offsite.form.sourcesHint") : $t("offsite.form.noSources")}</p>
  </fieldset>

  <Alert variant="warning">{$t("offsite.form.passwordWarning")}</Alert>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <Input
      id="offsite-password"
      type="password"
      label={$t("offsite.form.password")}
      bind:value={form.password}
      autocomplete="new-password"
      error={form.passwordTooShort ? $t("offsite.form.passwordTooShort", { n: MIN_PASSWORD_LENGTH }) : ""}
      hint={form.keepsPassword ? $t("offsite.form.passwordKept") : $t("offsite.form.passwordNeeded", { n: MIN_PASSWORD_LENGTH })}
    />
    <Input
      id="offsite-password-again"
      type="password"
      label={$t("offsite.form.passwordAgain")}
      bind:value={form.passwordAgain}
      autocomplete="new-password"
      error={form.passwordAgain && form.passwordsDiffer ? $t("offsite.form.passwordsDiffer") : ""}
    />
  </div>

  <!-- Retention and the check: two groups, each field with its unit, aligned on a subgrid (ADR-0432). -->
  <div class="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-x-8 gap-y-5">
    <FieldGroup legend={$t("offsite.form.retention")} description={$t("offsite.form.retentionHelp")} minWidth="7.5rem">
      <Input id="offsite-keep-daily" type="number" label={$t("offsite.form.daily")} bind:value={form.keepDaily} min={0} max={MAX_KEEP} boundsHint={false} steppers width="xs" />
      <Input id="offsite-keep-weekly" type="number" label={$t("offsite.form.weekly")} bind:value={form.keepWeekly} min={0} max={MAX_KEEP} boundsHint={false} steppers width="xs" />
      <Input id="offsite-keep-monthly" type="number" label={$t("offsite.form.monthly")} bind:value={form.keepMonthly} min={0} max={MAX_KEEP} boundsHint={false} steppers width="xs" />
    </FieldGroup>
    <FieldGroup legend={$t("offsite.form.check")} description={$t("offsite.form.checkHelp")} minWidth="9rem">
      <Input id="offsite-check-percent" type="number" label={$t("offsite.form.checkShare")} bind:value={form.checkPercent} suffix="%" min={0} max={100} steppers width="xs" />
    </FieldGroup>
  </div>

  <div class="flex flex-wrap gap-2">
    <Button
      type="submit"
      loading={saving}
      disabled={!canSave}
      disabledReason={saving ? "" : $t("offsite.form.incomplete")}
    >
      {saving ? $t("offsite.form.saving") : $t("offsite.form.save")}
    </Button>
    {#if answer.configured}
      <Button variant="danger" icon={Trash2} disabled={running !== null} onclick={onforget}>{$t("offsite.form.forget")}</Button>
    {/if}
  </div>
</form>
