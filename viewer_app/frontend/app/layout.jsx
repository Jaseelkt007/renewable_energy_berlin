import "./globals.css";

export const metadata = {
  title: "Roof Viewer",
  description: "Visualization bridge between the AI Renewable Designer roof pipeline and the upcoming M8 interactive viewer.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
