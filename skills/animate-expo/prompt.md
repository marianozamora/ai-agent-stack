Build React Native/Expo motion by deciding in order; stop at the first failed gate.

1. **Frequency**: tab switches, keyboard, scrolling and settings toggles get none (tabs never slide: `animation: 'none'`); tens/day only under 150ms.
2. **Purpose**: name it, or don't build it.
3. **Tool**: Reanimated (CSS transitions for state changes, shared values + `useAnimatedStyle` for gestures via `Gesture.Pan()`), native stack animations for screens; never core `Animated` or `PanResponder`.
4. **Properties**: `transform`/`opacity` only; never animate height, width, margin, BlurView intensity or elevation.
5. **Timing**: `Easing.bezier(0.23, 1, 0.32, 1)` for UI; springs for gestures with velocity handoff, velocity-or-distance dismissal, rubber-banding at bounds.
6. **UI thread**: no `setState` in gesture or scroll handlers, `scheduleOnRN` only in `onEnd` or at thresholds, `.get()`/`.set()` never during render, `'worklet'` on called functions.
7. **Press**: `scale 0.97` in 100-150ms, 44pt targets via `hitSlop`; one haptic per commit, on the same frame as the visual, never the only feedback.
8. **Reduced motion**: `useReducedMotion` or `ReduceMotion.System`.

Check `GestureHandlerRootView`, the New Architecture and the 120fps plist key. Judge feel on a release build on the slowest supported device.
