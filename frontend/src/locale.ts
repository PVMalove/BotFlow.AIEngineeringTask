import { useEffect, useState } from 'react';

type Locale = 'ru' | 'hy';

const messages = {
  ru: {
    tagline: 'Задачи команды через Telegram и веб-панель',
    preparing: 'Панель готовится к запуску.',
    language: 'Язык интерфейса',
    workspace: 'Рабочее пространство',
    board: 'Командная доска',
    boardDescription: 'Все задачи команды — в одном месте.',
    preparingLabel: 'Подготовка',
    preparingDescription:
      'Здесь можно будет следить за задачами команды и менять их статус.',
    workflow: 'От сообщения к результату',
    telegramTitle: 'Создавайте в Telegram',
    telegramDescription: 'Отправляйте боту текстовые и голосовые сообщения.',
    panelTitle: 'Управляйте на доске',
    panelDescription: 'Следите за ходом работы и обновляйте статус задач.',
    teamTitle: 'Работайте вместе',
    teamDescription: 'Общая картина работы для всей команды.',
  },
  hy: {
    tagline: 'Թիմի առաջադրանքները՝ Telegram-ի և վեբ վահանակի միջոցով',
    preparing: 'Վահանակը պատրաստվում է գործարկման։',
    language: 'Միջերեսի լեզուն',
    workspace: 'Աշխատանքային տարածք',
    board: 'Թիմի առաջադրանքների վահանակ',
    boardDescription: 'Թիմի բոլոր առաջադրանքները՝ մեկ տեղում։',
    preparingLabel: 'Նախապատրաստում',
    preparingDescription:
      'Այստեղ կկարողանաք հետևել թիմի առաջադրանքներին և փոխել դրանց կարգավիճակը։',
    workflow: 'Հաղորդագրությունից մինչև արդյունք',
    telegramTitle: 'Ստեղծեք Telegram-ում',
    telegramDescription:
      'Բոտին ուղարկեք տեքստային և ձայնային հաղորդագրություններ։',
    panelTitle: 'Կառավարեք վահանակում',
    panelDescription:
      'Հետևեք աշխատանքի ընթացքին և թարմացրեք առաջադրանքների կարգավիճակը։',
    teamTitle: 'Աշխատեք միասին',
    teamDescription: 'Աշխատանքի ընդհանուր պատկերը՝ ամբողջ թիմի համար։',
  },
};

const storageKey = 'botflow.locale';

function readLocale(): Locale {
  try {
    return window.localStorage.getItem(storageKey) === 'hy' ? 'hy' : 'ru';
  } catch {
    return 'ru';
  }
}

export function useLocale() {
  const [locale, setLocale] = useState<Locale>(readLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  function selectLocale(value: Locale) {
    setLocale(value);
    try {
      window.localStorage.setItem(storageKey, value);
    } catch {
      // The interface remains usable when browser storage is unavailable.
    }
  }

  return { locale, selectLocale, messages: messages[locale] };
}
