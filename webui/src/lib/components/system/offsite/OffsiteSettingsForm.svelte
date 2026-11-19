<script lang="ts">
  // The form of the off-device copy's target and of what it copies (ADR-0337, ADR-0422): saving
  // tests the target. The fields are those of `form`; the section saves, makes the key pair and
  // forgets the settings.
  import type { OffsiteSettingsAnswer, OffsiteStatus } from "$lib/api/offsite";
  import { Alert, Button, Input } from "$lib/components/ui";
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
</script>

<!-- The limits of a target that copies over the network: the same fields for every kind. -->
{#snippet limits()}
  <Input id="offsite-limit-upload" type="number" label={$t("offsite.form.limitUpload")} bind:value={form.limitUpload} hint={$t("offsite.form.limitHint")} />
  <Input id="offsite-limit-download" type="number" label={$t("offsite.form.limitDownload")} bind:value={form.limitDownload} hint={$t("offsite.form.limitHint")} />
{/snippet}

<form
  class="space-y-4"
  onsubmit={(e) => {
    e.preventDefault();
    onsave();
  }}
>
  <h4 class="font-medium text-sm">{$t("offsite.form.title")}</h4>
  <fieldset class="flex flex-wrap gap-x-6 gap-y-2 text-sm">
    <legend class="sr-only">{$t("offsite.form.kind")}</legend>
    <label class="flex items-center gap-2">
      <input type="radio" name="offsite-kind" value="disk" bind:group={form.kind} />
      {$t("offsite.form.disk")}
    </label>
    <label class="flex items-center gap-2">
      <input type="radio" name="offsite-kind" value="s3" bind:group={form.kind} />
      {$t("offsite.form.s3")}
    </label>
    <label class="flex items-center gap-2">
      <input type="radio" name="offsite-kind" value="rest" bind:group={form.kind} />
      {$t("offsite.form.rest")}
    </label>
    <label class="flex items-center gap-2">
      <input type="radio" name="offsite-kind" value="sftp" bind:group={form.kind} />
      {$t("offsite.form.sftp")}
    </label>
  </fieldset>

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
        <label class="flex items-center gap-2">
          <input type="checkbox" bind:checked={form.trustNewHostKey} />
          {$t("offsite.sftp.trustNewKey")}
        </label>
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
    {#each answer.pools as pool}
      <label class="flex items-center gap-2 break-all">
        <input type="checkbox" value={pool.path} bind:group={form.poolSources} />
        <span class="font-mono text-xs">{pool.path}</span>
      </label>
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

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <Input id="offsite-keep-daily" type="number" label={$t("offsite.form.keepDaily")} bind:value={form.keepDaily} />
    <Input id="offsite-keep-weekly" type="number" label={$t("offsite.form.keepWeekly")} bind:value={form.keepWeekly} />
    <Input id="offsite-keep-monthly" type="number" label={$t("offsite.form.keepMonthly")} bind:value={form.keepMonthly} />
    <Input id="offsite-check-percent" type="number" label={$t("offsite.form.checkPercent")} bind:value={form.checkPercent} />
  </div>

  <div class="flex flex-wrap gap-2">
    <Button type="submit" loading={saving} disabled={!canSave}>
      {saving ? $t("offsite.form.saving") : $t("offsite.form.save")}
    </Button>
    {#if answer.configured}
      <Button variant="danger" icon={Trash2} disabled={running !== null} onclick={onforget}>{$t("offsite.form.forget")}</Button>
    {/if}
  </div>
</form>
