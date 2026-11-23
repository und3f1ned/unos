// What the specs do with the shared components of $lib/components/ui (ADR-0432): an object's ⋯
// menu, the confirmations of confirm(), a card header's link and an empty state's next step, in
// English or Russian.
import type { Locator, Page } from '@playwright/test';
import { expect } from './fixtures';

/** The ⋯ menu button of the object `name` ("Actions for <name>"). */
export function actionsFor(page: Page | Locator, name: string, russian = false): Locator {
  return page.getByRole('button', { name: russian ? `Действия: ${name}` : `Actions for ${name}`, exact: true });
}

/** Choose `item` in the ⋯ menu of the object `name`. */
export async function chooseAction(page: Page, name: string, item: string | RegExp, russian = false): Promise<void> {
  await actionsFor(page, name, russian).click();
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  await menu.getByRole('menuitem', { name: item }).click();
}

/** The confirmation that confirm() shows, named by its question. */
export function confirmation(page: Page, title?: string | RegExp): Locator {
  return title === undefined ? page.getByRole('dialog').last() : page.getByRole('dialog', { name: title });
}

/** Answer the confirmation by its action's button `label` ("Delete backup"). */
export async function confirmWith(page: Page, label: string, title?: string | RegExp): Promise<void> {
  const dialog = confirmation(page, title);
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: label, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/** Cancel the confirmation: nothing happens. */
export async function cancelConfirmation(page: Page, title?: string | RegExp, label = 'Cancel'): Promise<void> {
  const dialog = confirmation(page, title);
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: label, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/**
 * The confirmation asked `title` with `message` (when given): answer it with its action's button
 * `action`, or cancel it with `cancel` (the Cancel button's text).
 */
export async function answerConfirmation(
  page: Page,
  { title, message, action, cancel }: { title: string; message?: string; action?: string; cancel?: string },
): Promise<void> {
  const dialog = confirmation(page, title);
  await expect(dialog).toBeVisible();
  if (message) await expect(dialog).toContainText(message);
  const button = cancel ?? action;
  if (!button) return;
  await dialog.getByRole('button', { name: button, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/**
 * A card header's link to the place where its things are managed (CardAction with `href`): named
 * `name` alone, the arrow an icon after it, leading to `href`.
 */
export async function expectCardLink(scope: Page | Locator, name: string, href: string): Promise<void> {
  const link = scope.locator('.card-header').getByRole('link', { name, exact: true });
  await expect(link).toHaveAttribute('href', href);
  await expect(link).toHaveClass(/card-action-link/);
  await expect(link).toHaveText(name);
  await expect(link.locator('svg[aria-hidden="true"]')).toHaveCount(1);
}

/** The next step of an empty state (EmptyState's `action`): a small secondary button, or a link drawn as one. */
export async function expectNextStep(
  scope: Page | Locator,
  role: 'button' | 'link',
  name: string,
  href?: string,
): Promise<Locator> {
  const step = scope.locator('.empty-state').getByRole(role, { name, exact: true });
  await expect(step).toBeVisible();
  await expect(step).toHaveClass(/btn-secondary/);
  await expect(step).toHaveClass(/btn-sm/);
  if (href !== undefined) await expect(step).toHaveAttribute('href', href);
  return step;
}
