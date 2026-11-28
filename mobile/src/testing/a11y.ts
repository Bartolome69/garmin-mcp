// What a screen reader would find on a rendered screen, read from the test renderer's JSON
// tree: the elements VoiceOver and TalkBack focus, in reading order, with their role, name,
// value and state. Used by the snapshot matrix and the focus-order walkthroughs. An
// approximation of the platforms' rules (accessible elements group their children; hidden
// subtrees are skipped; a Text outside any element is read on its own); the real check is
// the device walkthrough in docs/native-app/WALKTHROUGHS.md.
import { StyleSheet } from 'react-native';

import { numbersIn } from '../domain/replyGuard';
import { colors } from '../theme/tokens';
import { MIN_TOUCH } from '../ui/Button';

export type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };

export const isNode = (n: unknown): n is Node => !!n && typeof n === 'object' && !Array.isArray(n);
const kids = (n: Node): (Node | string)[] => n.children ?? [];

export function all(tree: unknown): Node[] {
  const out: Node[] = [];
  const walk = (n: unknown) => {
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!isNode(n)) return;
    out.push(n);
    kids(n).forEach(walk);
  };
  walk(tree);
  return out;
}

export const hasId = (n: unknown, id: string): boolean => {
  if (Array.isArray(n)) return n.some((c) => hasId(c, id));
  if (!isNode(n)) return false;
  return n.props?.testID === id || kids(n).some((c) => hasId(c, id));
};

/** Hidden from screen readers (and everything inside it). */
export const hidden = (n: Node) => {
  const p = n.props ?? {};
  const s = StyleSheet.flatten(p.style) ?? {};
  return p.accessibilityElementsHidden === true || p.importantForAccessibility === 'no-hide-descendants' || p['aria-hidden'] === true || s.display === 'none';
};

/** The words inside a node that a screen reader could see. */
export function words(n: unknown): string[] {
  if (typeof n === 'string') return [n];
  if (Array.isArray(n)) return n.flatMap(words);
  if (!isNode(n) || hidden(n)) return [];
  return kids(n).flatMap(words);
}

const SCREEN = /^RNS(Modal)?Screen$/; // a form sheet draws as RNSModalScreen
const TAB = /^RNSTabsScreen/;

/**
 * Where the case's screen is: `scope` is its tab (or the root stack when it isn't in one),
 * `front` the stack screen that holds it (a sheet's own screen), and `behind` every other
 * stack screen in the same scope (what a sheet sits over, or the screens pushed under it).
 */
export function locate(tree: unknown, id: string): { scope: Node; front: Node; behind: Node[] } {
  const path: Node[] = [];
  const find = (n: unknown, trail: Node[]): Node[] | null => {
    if (Array.isArray(n)) { for (const c of n) { const f = find(c, trail); if (f) return f; } return null; }
    if (!isNode(n)) return null;
    const next = [...trail, n];
    if (n.props?.testID === id) return next;
    return find(kids(n), next);
  };
  path.push(...(find(tree, []) ?? []));
  if (!path.length) throw new Error(`no ${id} on screen`);
  const tabAt = path.map((n) => TAB.test(n.type)).lastIndexOf(true);
  const screens = path.map((n, i) => (SCREEN.test(n.type) ? i : -1)).filter((i) => i >= 0);
  const front = path[screens[screens.length - 1]];
  // The scope: the tab when the screen is in one; otherwise the stack that holds the front screen.
  const scopeIndex = tabAt >= 0 ? tabAt : Math.max(0, screens.length > 1 ? screens[screens.length - 2] : 0);
  const scope = path[scopeIndex];
  const behind: Node[] = [];
  const collect = (n: unknown) => {
    if (Array.isArray(n)) { n.forEach(collect); return; }
    if (!isNode(n) || n === front) return;
    if (SCREEN.test(n.type) && !hasId(n, id)) { behind.push(n); return; }
    kids(n).forEach(collect);
  };
  collect(kids(scope));
  return { scope, front, behind };
}

export const brandFills = (tree: unknown, scheme: 'dark' | 'light') =>
  all(tree).filter((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors[scheme].brand).length;

export type A11yElement = { role: string; label: string; value?: string; state?: string[]; live?: string; id?: string };

const FOCUSABLE_TYPES = new Set(['TextInput', 'RCTSwitch', 'AndroidSwitch', 'RNCSegmentedControl']);

function element(n: Node): A11yElement {
  const p = n.props;
  const role = p.accessibilityRole ?? (n.type === 'RNCSegmentedControl' ? 'tablist' : n.type === 'TextInput' ? 'textfield' : /Switch$/.test(n.type) ? 'switch' : n.type === 'Text' ? 'text' : 'group');
  const label = String(p.accessibilityLabel ?? words(n).join(' ')).replace(/\s+/g, ' ').trim();
  const v = p.accessibilityValue;
  const value = v?.text ?? (typeof v?.now === 'number' ? `${v.now}${typeof v.max === 'number' ? ` of ${v.max}` : ''}` : undefined)
    ?? (n.type === 'RNCSegmentedControl' && Array.isArray(p.values) ? `${p.values[p.selectedIndex ?? 0]} (${p.values.join(', ')})` : undefined)
    ?? (n.type === 'TextInput' && p.value ? String(p.value) : undefined) ?? (/Switch$/.test(n.type) ? ((p.value ?? p.on) ? 'on' : 'off') : undefined)
    ?? (role === 'switch' && p.accessibilityState ? (p.accessibilityState.checked ? 'on' : 'off') : undefined);
  const st = p.accessibilityState ?? {};
  const state = Object.entries(st).filter(([k, on]) => (on === true || on === 'mixed') && !(role === 'switch' && k === 'checked')).map(([k]) => k);
  if (p.disabled === true && !state.includes('disabled')) state.push('disabled');
  const live = p.accessibilityLiveRegion && p.accessibilityLiveRegion !== 'none' ? p.accessibilityLiveRegion : undefined;
  return { role, label, ...(value ? { value } : {}), ...(state.length ? { state } : {}), ...(live ? { live } : {}), ...(p.testID ? { id: p.testID } : {}) };
}

/** The elements a screen reader focuses, in reading order (the order they're drawn in the tree). */
export function a11yElements(tree: unknown): A11yElement[] {
  const out: A11yElement[] = [];
  const walk = (n: unknown) => {
    if (typeof n === 'string') { if (n.trim()) out.push({ role: 'text', label: n.trim() }); return; }
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!isNode(n) || hidden(n)) return;
    const p = n.props ?? {};
    if (/^RNSVG/.test(n.type)) return; // drawings: said by the labelled element around them
    const focusable = p.accessible === true || FOCUSABLE_TYPES.has(n.type) || /Switch$/.test(n.type)
      || (!!p.accessibilityRole && p.accessibilityRole !== 'none' && (n.type === 'Text' || !!p.onClick || !!p.onPress || !!p.accessibilityLabel));
    // A focusable element with no name is kept (the checks flag it), unless it holds nothing
    // a screen reader could reach and does nothing (a plain accessible wrapper).
    if (focusable) { const e = element(n); if (e.label || e.role !== 'group' || isControl(n)) out.push(e); return; }
    if (n.type === 'Text') {
      // A Text is read as its label when it has one ("Repeat 10 times" for "10 ×").
      const t = String(p.accessibilityLabel ?? words(n).join('')).replace(/\s+/g, ' ').trim();
      if (t) out.push({ role: 'text', label: t, ...(p.accessibilityLiveRegion && p.accessibilityLiveRegion !== 'none' ? { live: p.accessibilityLiveRegion } : {}) });
      return;
    }
    kids(n).forEach(walk);
  };
  walk(tree);
  return out;
}

/** The stack header's title for the front screen, read first (it's above the content). */
export function headerTitle(front: Node): string | null {
  const config = all(front).find((n) => n.type === 'RNSScreenStackHeaderConfig');
  if (!config || config.props.hidden) return null;
  const t = config.props.title;
  return typeof t === 'string' && t.trim() ? t.trim() : null;
}

export const line = (e: A11yElement) => `${e.role} "${e.label}"${e.value ? ` = ${e.value}` : ''}${e.state ? ` [${e.state.join(', ')}]` : ''}${e.live ? ` (live ${e.live})` : ''}`;

// -- checks ------------------------------------------------------------------------------------

const PRESS = ['onClick', 'onPress', 'onLongPress', 'onResponderGrant'] as const;
const NOT_CONTROLS = /^(RCTScrollView|AndroidHorizontalScrollView|RCTScrollContentView|AndroidHorizontalScrollContentView|RNSScreen.*|RNSModalScreen)$/;
/**
 * Something a finger can work: a native switch, slider or text field, or any view or text
 * that answers a press (with or without a role: a Pressable with no role is still a target).
 */
export const isControl = (n: Node) => {
  if (/Switch$|^TextInput$|Slider$/.test(n.type)) return true;
  if (NOT_CONTROLS.test(n.type)) return false;
  return PRESS.some((k) => typeof n.props?.[k] === 'function') || typeof n.props?.onChange === 'function' || typeof n.props?.onValueChange === 'function';
};
const slopV = (h: unknown) => (typeof h === 'number' ? 2 * h : h && typeof h === 'object' ? ((h as { top?: number }).top ?? 0) + ((h as { bottom?: number }).bottom ?? 0) : 0);
const slopH = (h: unknown) => (typeof h === 'number' ? 2 * h : h && typeof h === 'object' ? ((h as { left?: number }).left ?? 0) + ((h as { right?: number }).right ?? 0) : 0);
const describeNode = (n: Node) => n.props.testID ?? n.props.accessibilityLabel ?? words(n).join(' ').slice(0, 40) ?? n.type;
// A native switch's own frame when nothing sizes it: UISwitch is 51 × 31 pt.
const SWITCH = { width: 51, height: 31 };

/**
 * Controls smaller than MIN_TOUCH, tall or (where the width is set) wide, hit slop counted.
 * A control inside another one isn't checked on its own: the outer one is the target (a
 * switch row's switch).
 */
export function smallTargets(tree: unknown): string[] {
  const out: string[] = [];
  const check = (n: Node) => {
    const s = StyleSheet.flatten(n.props.style) ?? {};
    const sw = /Switch$/.test(n.type);
    const inner = all(n).map((x) => StyleSheet.flatten(x.props?.style) ?? {});
    const height = Math.max(s.minHeight ?? 0, s.height ?? 0, sw ? SWITCH.height : 0,
      ...inner.map((x) => Math.max(x.minHeight ?? 0, x.height ?? 0, x.lineHeight ?? 0))) + slopV(n.props.hitSlop);
    // Width only where it's set on the control itself (a row or text button takes the width it needs).
    const set = [s.width, s.minWidth].filter((w): w is number => typeof w === 'number');
    const width = sw && !set.length ? SWITCH.width : set.length ? Math.max(...set) + slopH(n.props.hitSlop) : Infinity;
    if (height < MIN_TOUCH || width < MIN_TOUCH) out.push(`${describeNode(n)}: ${width === Infinity ? '' : `${width} × `}${height}`);
  };
  const walk = (n: unknown) => {
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!isNode(n)) return;
    if (isControl(n)) { check(n); return; }
    kids(n).forEach(walk);
  };
  walk(tree);
  return [...new Set(out)];
}

// Names two controls may share on purpose: a row of choices repeated per item (each sits under its own heading).
export const SHARED_NAMES = new Set(['Reconnect', 'Helpful', 'Not helpful', 'Edit', 'Remove', 'Request it', 'These are different runs']);
const NAMED_ROLES = new Set(['button', 'link', 'radio', 'tab', 'checkbox', 'switch', 'adjustable', 'menuitem', 'tablist']);

/** Focusable elements a screen reader would land on and find nothing to say. */
export const unnamed = (els: A11yElement[]) => els.filter((e) => e.role !== 'text' && !e.label).map((e) => `${e.role} ${e.id ?? ''}`.trim());

/** Controls that share a name (a screen reader can't tell them apart), bar SHARED_NAMES. */
export function sharedNames(els: A11yElement[]): string[] {
  const names = els.filter((e) => NAMED_ROLES.has(e.role)).map((e) => e.label);
  return [...new Set(names.filter((n, i) => names.indexOf(n) !== i && !SHARED_NAMES.has(n)))];
}

const scales = (t: Node) => t.props.allowFontScaling !== false && !(t.props.maxFontSizeMultiplier && t.props.maxFontSizeMultiplier <= 1.5);

/** Text that clips at large sizes: scalable text inside a box of fixed height. */
export function clipped(tree: unknown): string[] {
  const out: string[] = [];
  for (const n of all(tree)) {
    const s = StyleSheet.flatten(n.props?.style) ?? {};
    if (n.type === 'View' && typeof s.height === 'number' && s.height < 80) {
      const inside = all(n).filter((x) => x !== n && x.type === 'Text' && scales(x) && words(x).some((t) => /\w/.test(t)));
      if (inside.length) out.push(`${describeNode(n)}: height ${s.height}`);
    }
  }
  return [...new Set(out)];
}

/** Scalable text cut to a number of lines (an ellipsis at large sizes, by design: recorded, not failed). */
export function truncated(tree: unknown): string[] {
  return [...new Set(all(tree).filter((n) => n.type === 'Text' && typeof n.props.numberOfLines === 'number' && scales(n))
    .map((n) => `${words(n).join('').slice(0, 30)} (${n.props.numberOfLines} line${n.props.numberOfLines === 1 ? '' : 's'})`))];
}

// The Coach's blocks (absent with the Coach off), and its words.
export const COACH_SLOTS = new Set(['coach', 'coach-line', 'ask-why', 'coach-note', 'coach-ask', 'debrief', 'diff-coach']);
// Chrome that's there only with the Coach, not a training number: the paywall's Coach row.
const COACH_CHROME = new Set(['coach-day', 'coach-remaining', 'plus-item-coach']);

/** Anything of the Coach's on this part of the screen: its blocks, or its name. */
export function coachSurfaces(tree: unknown): string[] {
  const out: string[] = [];
  for (const n of all(tree)) {
    if (COACH_SLOTS.has(n.props?.testID) || COACH_CHROME.has(n.props?.testID)) out.push(`block ${n.props.testID}`);
  }
  for (const e of a11yElements(tree)) if (/\bCoach\b/.test(e.label)) out.push(`${e.role} "${e.label.slice(0, 60)}"`);
  return [...new Set(out)];
}

/** Every number drawn or said, outside the Coach's blocks. */
export function plainNumbers(tree: unknown): string[] {
  const out: string[] = [];
  const walk = (n: unknown) => {
    if (typeof n === 'string') { out.push(...numbersIn(n)); return; }
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!isNode(n)) return;
    const id = n.props?.testID;
    if (COACH_SLOTS.has(id) || COACH_CHROME.has(id)) return;
    for (const said of [n.props?.accessibilityLabel, n.props?.accessibilityValue?.text]) if (typeof said === 'string') out.push(...numbersIn(said));
    kids(n).forEach(walk);
  };
  walk(tree);
  return out;
}
