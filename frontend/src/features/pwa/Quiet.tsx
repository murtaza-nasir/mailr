// An error boundary for the phone frame's extras (tab bar, banners, sheets): if one fails, it
// draws nothing and the app keeps working.
import { Component, type ReactNode } from 'react'

export class Quiet extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(e: unknown) {
    console.warn('mailr: a phone-frame part failed', e)
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}
