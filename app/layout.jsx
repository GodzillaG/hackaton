import "./globals.css";

export const metadata = {
  title: "ScoliScan School",
  description: "Мобильный ИИ-комплекс для скрининга сколиоза у школьников"
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
