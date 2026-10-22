import { useEffect, useState } from 'react';

export type Locale = 'ru' | 'hy';
export type Messages = (typeof messages)['ru'];

const messages = {
  ru: {
    adminTitle: 'Управление доступом',
    adminDescription:
      'Рассматривайте заявки и управляйте доступом участников к команде.',
    joinRequests: 'Заявки',
    rejectedRequests: 'Отклонённые',
    members: 'Участники',
    accessPending: 'Ожидает решения',
    accessRejected: 'Отклонён',
    accessApproved: 'Допущен',
    accessBlocked: 'Заблокирован',
    roleAdmin: 'Администратор',
    roleUser: 'Участник',
    approve: 'Одобрить',
    reject: 'Отклонить',
    block: 'Заблокировать',
    unblock: 'Разблокировать',
    requestedAt: 'Дата заявки',
    unnamedMember: 'Имя не указано',
    accessLoading: 'Загружаем список…',
    accessLoadFailed:
      'Не удалось обновить список. Проверьте связь и перечитайте данные.',
    adminDenied: 'Нет доступа к управлению участниками. Проверяем вашу сессию.',
    decisionSaved: 'Решение сохранено.',
    decisionUnchanged: 'Такое решение уже принято. Данные перечитаны.',
    decisionConflict:
      'Данные изменились или решение уже недоступно. Список перечитан — проверьте текущее состояние перед новым действием.',
    decisionUnknown:
      'Результат действия не подтверждён. Перечитываем список. При ошибке обновите данные; не повторяйте действие, пока не проверите состояние.',
    cannotBlockSelf: 'Нельзя заблокировать собственную учётную запись.',
    lastAdmin: 'Нельзя заблокировать последнего администратора.',
    savingDecision: 'Сохраняем решение и обновляем список…',
    noRequests: 'Заявок нет.',
    noMembers: 'Участников нет.',
    refreshAccess: 'Обновить список',
    previousPage: 'Назад',
    nextPage: 'Далее',
    page: 'Страница',
    sessionLoading: 'Проверяем сессию…',
    retry: 'Повторить',
    loginTitle: 'Вход в BotFlow',
    loginCode: 'Код из Telegram',
    codeFormat: 'Введите код из 32 символов: латинские буквы, цифры, _ или -.',
    loginInstructions:
      'Отправьте /login боту в личном чате и вставьте одноразовый код. Он действует 5 минут.',
    signIn: 'Войти',
    signingIn: 'Входим…',
    signOut: 'Выйти',
    showPassword: 'Показать пароль',
    hidePassword: 'Скрыть пароль',
    requestFailed: 'Не удалось выполнить запрос. Попробуйте ещё раз.',
    invalidCode:
      'Код недействителен или истёк. Получите новый через /login в Telegram.',
    rateLimited: 'Слишком много попыток. Подождите минуту и попробуйте снова.',
    serviceUnavailable: 'Сервис входа временно недоступен. Попробуйте позже.',
    networkError:
      'Нет связи с сервером. Проверьте подключение и попробуйте снова.',
    accessDenied:
      'Запрос отклонён. Обновите страницу; если ошибка повторится, обратитесь к администратору.',
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
    adminTitle: 'Մուտքի կառավարում',
    adminDescription: 'Դիտարկեք հայտերը և կառավարեք մասնակիցների մուտքը թիմ։',
    joinRequests: 'Հայտեր',
    rejectedRequests: 'Մերժվածներ',
    members: 'Մասնակիցներ',
    accessPending: 'Սպասում է որոշման',
    accessRejected: 'Մերժված',
    accessApproved: 'Մուտքը թույլատրված է',
    accessBlocked: 'Արգելափակված',
    roleAdmin: 'Ադմինիստրատոր',
    roleUser: 'Մասնակից',
    approve: 'Հաստատել',
    reject: 'Մերժել',
    block: 'Արգելափակել',
    unblock: 'Ապաարգելափակել',
    requestedAt: 'Հայտի ամսաթիվը',
    unnamedMember: 'Անունը նշված չէ',
    accessLoading: 'Բեռնում ենք ցանկը…',
    accessLoadFailed:
      'Ցանկը չհաջողվեց թարմացնել։ Ստուգեք կապը և կրկին բեռնեք տվյալները։',
    adminDenied:
      'Մասնակիցներին կառավարելու իրավունք չունեք։ Ստուգում ենք ձեր մուտքը։',
    decisionSaved: 'Որոշումը պահպանված է։',
    decisionUnchanged:
      'Այս որոշումն արդեն ընդունված է։ Տվյալները կրկին բեռնված են։',
    decisionConflict:
      'Տվյալները փոխվել են կամ որոշումն այլևս հասանելի չէ։ Ցանկը կրկին բեռնված է․ նոր գործողությունից առաջ ստուգեք ընթացիկ վիճակը։',
    decisionUnknown:
      'Գործողության արդյունքը հաստատված չէ։ Կրկին բեռնում ենք ցանկը։ Սխալի դեպքում թարմացրեք տվյալները․ մի կրկնեք գործողությունը, մինչև չստուգեք վիճակը։',
    cannotBlockSelf: 'Չեք կարող արգելափակել ձեր սեփական հաշիվը։',
    lastAdmin: 'Չի կարելի արգելափակել վերջին ադմինիստրատորին։',
    savingDecision: 'Պահպանում ենք որոշումը և թարմացնում ցանկը…',
    noRequests: 'Հայտեր չկան։',
    noMembers: 'Մասնակիցներ չկան։',
    refreshAccess: 'Թարմացնել ցանկը',
    previousPage: 'Նախորդը',
    nextPage: 'Հաջորդը',
    page: 'Էջ',
    sessionLoading: 'Ստուգում ենք մուտքը…',
    retry: 'Կրկին փորձել',
    loginTitle: 'Մուտք BotFlow',
    loginCode: 'Telegram-ի կոդը',
    codeFormat: 'Մուտքագրեք 32 նիշից կազմված կոդը՝ լատինատառ, թվեր, _ կամ -։',
    loginInstructions:
      'Բոտին անձնական զրույցում ուղարկեք /login և տեղադրեք մեկանգամյա կոդը։ Այն գործում է 5 րոպե։',
    signIn: 'Մուտք գործել',
    signingIn: 'Մուտք ենք գործում…',
    signOut: 'Դուրս գալ',
    showPassword: 'Ցուցադրել գաղտնաբառը',
    hidePassword: 'Թաքցնել գաղտնաբառը',
    requestFailed: 'Հարցումը չհաջողվեց։ Փորձեք կրկին։',
    invalidCode:
      'Կոդն անվավեր է կամ ժամկետանց։ Telegram-ում /login հրամանով ստացեք նորը։',
    rateLimited: 'Չափազանց շատ փորձեր։ Սպասեք մեկ րոպե և փորձեք կրկին։',
    serviceUnavailable:
      'Մուտքի ծառայությունը ժամանակավորապես անհասանելի է։ Փորձեք ավելի ուշ։',
    networkError: 'Սերվերի հետ կապ չկա։ Ստուգեք կապը և փորձեք կրկին։',
    accessDenied:
      'Հարցումը մերժվել է։ Թարմացրեք էջը։ Եթե սխալը կրկնվի, դիմեք ադմինիստրատորին։',
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
