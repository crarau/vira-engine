import type { Metadata } from "next";

/**
 * The judge surface is not part of the console.
 *
 * Everything else in this app is a dense operator tool with a nav bar carrying
 * Generate / Corpus / Library and a live API-health dot. A paid panellist is
 * none of those things: they arrived from Terac, they have a few minutes, and
 * a link to "Corpus" is at best noise and at worst an invitation to wander
 * into the engine's own scores — which is the one thing that would spoil the
 * measurement this page exists to take.
 *
 * Next.js only allows a second root layout when there is no `app/layout.tsx`,
 * and editing that file to carve out this route would put a judge-shaped
 * conditional in the console's shell. So the escape is done here instead: a
 * fixed, opaque, full-viewport surface that covers the nav and ignores the
 * console's `max-w-[1600px]` centred padding. It unmounts on navigation, so
 * nothing about it leaks into the operator pages.
 */

export const metadata: Metadata = {
  title: "Rate these ads",
  description: "Watch five short ads and say which one you would stop for.",
  // A judge link is unguessable by design. Keeping it out of an index is the
  // cheap half of that; the token is the real lock.
  robots: { index: false, follow: false },
};

export default function JudgeLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `id` because this element, not the document, is what scrolls — the page
    // scrolls it back to the top when the judge moves to the next film.
    <div
      id="judge-surface"
      className="fixed inset-0 z-50 overflow-y-auto overscroll-contain bg-zinc-950 text-zinc-100"
    >
      {children}
    </div>
  );
}
