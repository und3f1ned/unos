import { File, FileAudio, FileImage, FileText, FileVideo, Folder, Link } from "lucide-svelte";
import type { FileInfo, TrashItem } from "$lib/api/files";
import type { IconComponent } from "$lib/components/ui";

// The icons of the items that the Files page and its trash list.

/** A folder, or a file by the kind that its extension names. */
export function fileIcon(item: FileInfo): IconComponent {
  if (item.type === "directory") return Folder;
  const ext = item.name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return FileImage;
  if (["mp4", "mkv", "avi", "mov", "webm"].includes(ext)) return FileVideo;
  if (["mp3", "flac", "wav", "ogg", "m4a"].includes(ext)) return FileAudio;
  if (["txt", "md", "json", "xml", "yaml", "yml"].includes(ext)) return FileText;
  return File;
}

/** A folder, a link or a file in the trash. */
export function trashItemIcon(item: TrashItem): IconComponent {
  if (item.type === "directory") return Folder;
  if (item.type === "symlink") return Link;
  return File;
}
