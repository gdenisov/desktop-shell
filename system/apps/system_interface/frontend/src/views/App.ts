import m from "mithril";
import { DesktopShell } from "./desktop/DesktopShell";
import { UpdateStalenessBanner } from "./UpdateStalenessBanner";

export function App(): m.Component {
  return {
    view() {
      return m(
        "div",
        // h-screen is the full viewport: inside the minds desktop shell this app renders in a
        // sandboxed iframe the shell already places below its title bar.
        { class: "app-layout flex h-screen flex-col" },
        [
          m(UpdateStalenessBanner),
          // min-h-0: a flex item's automatic minimum size is its content's, so without this the
          // desktop can grow with the viewport but never shrink back.
          m("div", { class: "app-main min-h-0 flex-1" }, m(DesktopShell)),
        ],
      );
    },
  };
}
