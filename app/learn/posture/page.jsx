const CHECKLIST = [
  {
    title: "Стопы на опоре",
    body: "Стопы полностью стоят на полу или подставке, колени примерно под прямым углом."
  },
  {
    title: "Экран на уровне глаз",
    body: "Верхняя треть экрана находится близко к уровню глаз, чтобы шея не уходила вперёд."
  },
  {
    title: "Плечи расслаблены",
    body: "Локти лежат рядом с корпусом, плечи не подняты, кисти не висят в воздухе."
  },
  {
    title: "Спина с опорой",
    body: "Корпус не заваливается на одну сторону, поясница получает мягкую поддержку."
  }
];

const ROUTINE = [
  "Каждые 30-40 минут встать на 60 секунд.",
  "Сделать 5 спокойных вдохов с раскрытием грудной клетки.",
  "Проверить, что рюкзак носится на двух лямках.",
  "Повторять скрининг по школьному графику или при заметном изменении осанки."
];

export default function PostureGuidePage() {
  return (
    <main className="resourcePage">
      <header className="resourceTopbar">
        <a className="resourceBrand" href="/">
          <span>S</span>
          ScolioScan School
        </a>
        <a className="resourceLink" href="/">
          Открыть скрининг
        </a>
      </header>

      <section className="postureHero">
        <img src="/education/posture-hero.png" alt="" />
        <div className="postureHeroContent">
          <p className="eyebrow">Осанка за партой</p>
          <h1>Рабочее место, которое снижает лишнюю нагрузку на спину</h1>
          <p>
            Короткий школьный чеклист для уроков, домашней работы и цифровых занятий. Он помогает быстрее заметить
            асимметрию и не тратить время детей на лишние очереди.
          </p>
        </div>
      </section>

      <section className="resourceBand">
        <div className="resourceSectionHeader">
          <p className="eyebrow">Быстрая проверка</p>
          <h2>4 ориентира правильной посадки</h2>
        </div>
        <div className="postureChecklist">
          {CHECKLIST.map((item, index) => (
            <article key={item.title}>
              <strong>{index + 1}</strong>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="resourceSplit">
        <div>
          <p className="eyebrow">Школьная рутина</p>
          <h2>Мини-паузы лучше длинного ожидания</h2>
          <p>
            Цель ScolioScan School — быстро отфильтровать учеников, которым нужен очный осмотр, и не держать остальных
            в очередях без причины.
          </p>
        </div>
        <ol className="routineList">
          {ROUTINE.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      </section>

      <section className="resourceBand compact">
        <div className="resourceSectionHeader">
          <p className="eyebrow">Когда запускать скрининг</p>
          <h2>После заметной асимметрии, жалоб на спину или по школьному графику</h2>
        </div>
        <div className="resourceMetricRow">
          <div>
            <span>Basic</span>
            <strong>1 фото</strong>
            <p>Быстрый первичный фильтр.</p>
          </div>
          <div>
            <span>Advanced</span>
            <strong>5 ракурсов</strong>
            <p>Полный школьный протокол с тестом Адамса.</p>
          </div>
          <div>
            <span>Corporate</span>
            <strong>по договору</strong>
            <p>Лимиты школы доступны после подтверждения ученика.</p>
          </div>
        </div>
      </section>
    </main>
  );
}
