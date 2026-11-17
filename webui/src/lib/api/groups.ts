import { api } from "./client";
import type { ShareAccessLevel, SmbAccountState } from "./shares";

// Groups of users (supervisor/src/shares/groups.py). A group lives in the UNOS database
// only; a share's group access gives each member whose SMB account is active the group's level,
// or the higher level the member has of their own. A request names a group by its id, which is
// never handed out twice, with the name the page saw, and its members by id with their usernames:
// the server answers 409 when one of them now has another name. Names are unique in any case.

/** The rule of group names: the rule of usernames. */
export const GROUP_NAME_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;

export interface GroupMember {
  user_id: number;
  username: string;
  smb_state: SmbAccountState;
}

/** A share the group has access to. */
export interface GroupShare {
  share: string;
  level: ShareAccessLevel;
}

export interface Group {
  id: number;
  name: string;
  /** Send it back as `expected_version` when changing the group. */
  version: number;
  /** Sorted by username. */
  members: GroupMember[];
  /** Sorted by share name. */
  shares: GroupShare[];
  /** Only in answers to changes. */
  warnings?: string[];
}

/** A member sent to the server: the server answers 409 when the id now belongs to someone else. */
export interface GroupMemberEntry {
  user_id: number;
  username: string;
}

export interface GroupCreate {
  name: string;
  members: GroupMemberEntry[];
}

/** PATCH body: only the fields that are sent change. */
export interface GroupUpdate {
  /** The group's name as the page saw it: the server answers 409 when it has another now. */
  name: string;
  /** The server answers 409 when the group's version differs. */
  expected_version?: number;
  new_name?: string;
  /** The complete list: it replaces the group's members. */
  members?: GroupMemberEntry[];
}

export interface GroupDeleted {
  status: "deleted";
  warnings: string[];
}

function groupPath(id: number): string {
  return `/groups/${id}`;
}

export async function getGroups(): Promise<Group[]> {
  return api.get<Group[]>("/groups");
}

export async function createGroup(group: GroupCreate): Promise<Group> {
  return api.post<Group>("/groups", group);
}

export async function updateGroup(id: number, patch: GroupUpdate): Promise<Group> {
  return api.patch<Group>(groupPath(id), patch);
}

/** `name`: the group's name as the page saw it (409 when it has another now). */
export async function deleteGroup(id: number, name: string): Promise<GroupDeleted> {
  return api.delete<GroupDeleted>(`${groupPath(id)}?name=${encodeURIComponent(name)}`);
}
