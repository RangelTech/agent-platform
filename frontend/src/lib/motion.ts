/**
 * Shared Framer Motion tokens and variants.
 *
 * Durations follow the skill's motion scale:
 * - fast: micro-feedback (hover states, small toggles) — handled inline where needed
 * - normal: default transitions (page/section/list entrances)
 * - slow: complex/storytelling transitions (rarely needed here)
 *
 * Easing follows a standard "ease-out" curve for entrances (feels quick then settles)
 * and a slightly different curve for exits (quick start, no lingering).
 */
import type { Transition, Variants } from 'framer-motion'

export const DURATION = {
  fast: 0.15,
  normal: 0.24,
  slow: 0.4,
} as const

export const EASING = {
  standard: [0.16, 1, 0.3, 1] as const, // ease-out — entrances
  exit: [0.4, 0, 1, 1] as const, // ease-in — exits
}

export const pageTransition: Transition = {
  duration: DURATION.normal,
  ease: EASING.standard,
}

/** Page-level fade + subtle vertical slide, used to wrap route content. */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: pageTransition },
  exit: { opacity: 0, y: -4, transition: { duration: DURATION.fast, ease: EASING.exit } },
}

/** Fade + slide-up for chat messages, dashboard tiles, and list rows appearing on load. */
export const fadeUp: Variants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: pageTransition },
}

/** Staggered container for groups of items (tiles, list rows) entering together. */
export function staggerContainer(stagger = 0.05): Variants {
  return {
    initial: {},
    animate: {
      transition: { staggerChildren: stagger },
    },
  }
}

/** Simple fade only — for indicators, badges, and lightweight elements. */
export const fadeIn: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: DURATION.fast, ease: EASING.standard } },
  exit: { opacity: 0, transition: { duration: DURATION.fast, ease: EASING.exit } },
}
