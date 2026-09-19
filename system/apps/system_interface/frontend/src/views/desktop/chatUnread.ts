/**
 * Which chats have finished a turn the user has not looked at yet: the "done, come back to me"
 * state the chat list shows, so a chat that answered while the user was elsewhere stands out
 * from one merely sitting idle.
 *
 * Read off the inventory: an instance whose status goes from working to anything else while no
 * window on this desktop is showing it on screen is marked, and the mark goes when a window
 * shows it, or when it starts working again (a new turn makes the old reply old news). Kept per
 * browser, in storage, so it survives a reload and every desktop tab of this browser agrees.
 */

const STORAGE_KEY = "si-chat-unread";

let unreadAddresses = new Set<string>();
let lastStatusByAddress = new Map<string, string>();

function load(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function save(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...unreadAddresses]));
  } catch {
    /* A browser that refuses storage still gets the state for this page's lifetime. */
  }
}

export function initChatUnread(): void {
  unreadAddresses = load();
  window.addEventListener("storage", (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) unreadAddresses = load();
  });
}

/** Fold one inventory push in: ``statusByAddress`` is every listed instance's status, and
 *  ``isOnScreen`` whether a window here is showing the address right now. */
export function noteStatuses(
  statusByAddress: ReadonlyMap<string, string>,
  isOnScreen: (address: string) => boolean,
): void {
  let isChanged = false;
  for (const [address, status] of statusByAddress) {
    const previous = lastStatusByAddress.get(address);
    if (status === "working") {
      if (unreadAddresses.delete(address)) isChanged = true;
    } else if (previous === "working" && !isOnScreen(address)) {
      unreadAddresses.add(address);
      isChanged = true;
    }
  }
  for (const address of unreadAddresses) {
    if (!statusByAddress.has(address) || isOnScreen(address)) {
      unreadAddresses.delete(address);
      isChanged = true;
    }
  }
  lastStatusByAddress = new Map(statusByAddress);
  if (isChanged) save();
}

export function isUnread(address: string): boolean {
  return unreadAddresses.has(address);
}

/** The user is looking at it: whatever it finished has been seen. */
export function markRead(address: string): void {
  if (unreadAddresses.delete(address)) save();
}
