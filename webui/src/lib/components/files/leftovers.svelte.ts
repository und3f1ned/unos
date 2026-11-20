import { listUploadLeftovers, removeUploadLeftovers, type UploadLeftover } from "$lib/api/files";
import { messageOf, signedOut } from "./errors";

/**
 * The hidden files that uploads left when the supervisor stopped in the middle of them, as its
 * last check found them (ADR-0354): the notice above the list offers to remove them. The Remove
 * dialog: the files it is about while it is open, whether the removal is on its way, and the
 * server's refusal.
 */
export class UploadLeftovers {
  files = $state.raw<UploadLeftover[]>([]);
  removing = $state.raw<UploadLeftover[] | null>(null);
  sending = $state(false);
  error = $state("");
  // The first answer has come (or failed): the page draws the notice with the list (ADR-0430).
  loaded = $state(false);
  size = $derived(this.files.reduce((sum, file) => sum + file.size, 0));

  // Without an answer the page shows no notice; the rest of it works as before.
  async load() {
    try {
      this.files = (await listUploadLeftovers()).files;
    } catch {
      this.files = [];
    } finally {
      this.loaded = true;
    }
  }

  /** Opens the dialog about the files listed now. */
  ask() {
    this.removing = this.files;
    this.error = "";
  }

  /**
   * Removes the files of the dialog. The server checks every file before it removes any: a refusal
   * keeps the dialog open with its reason, about the files still listed. The server drops from its
   * list the file it refused and those that are gone, so Remove for good does not meet the same
   * refusal again; with none left, the dialog closes and the answer is "unlisted", for the page to
   * show the reason above the list. "signed-out": a 401 has ended the session.
   */
  async remove(): Promise<"done" | "unlisted" | "signed-out"> {
    const files = this.removing;
    if (!files) return "done";
    this.error = "";
    this.sending = true;
    try {
      await removeUploadLeftovers(files.map((file) => file.path));
      this.removing = null;
    } catch (e) {
      if (signedOut(e)) return "signed-out";
      this.error = messageOf(e);
    } finally {
      this.sending = false;
    }
    await this.load();
    if (this.removing === null) return "done";
    if (this.files.length > 0) {
      this.removing = this.files;
      return "done";
    }
    this.removing = null;
    return "unlisted";
  }
}
