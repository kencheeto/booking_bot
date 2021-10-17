from django.core.management.base import BaseCommand

from django.core.mail import EmailMessage

# from django.contrib.auth.models import User
# from tennis.models import UserProfile
# from tennis.models import CourtLocation
# from tennis.models import BookingParameter
from tennis.models import Booking

import time
import pytz
import datetime
import os

# import pytest

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec
# from selenium.webdriver.common.keys import Keys
# from selenium.webdriver.common.action_chains import ActionChains
# from selenium.webdriver.support import expected_conditions
# from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

from .spotery_constants import ROOT_URL
from .spotery_constants import LOCAL_TIME_ZONE
from .spotery_constants import MAX_LOOKAHEAD_DAYS
from .spotery_constants import CALENDAR_ADVANCE_TIME
from .spotery_constants import LONG_POLE_WAIT
from .spotery_constants import DRIVER_WAIT

from booking_bot.settings import EMAIL_HOST_USER

pacific = pytz.timezone('US/Pacific')


def check_desired_date(booking_datetime):
    now = LOCAL_TIME_ZONE.localize(datetime.datetime.now(), is_dst=True)

    max_booking_date = now + datetime.timedelta(days=MAX_LOOKAHEAD_DAYS,
                                                hours=-CALENDAR_ADVANCE_TIME)

    if booking_datetime.date() > max_booking_date.date():
        raise ValueError(
            'Desired booking date {} is more than {} days ahead of current datetime, {}'
            .format(booking_datetime.strftime("%a, %b %d, %Y at %I:%M %p %Z"),
                    MAX_LOOKAHEAD_DAYS,
                    now.strftime("%a, %b %d, %Y at %I:%M %p %Z")))


def authenticate(driver, root_url, login_email, login_password):
    driver.get(root_url)
    driver.find_element(By.LINK_TEXT, "login / sign up").click()

    # Since we're loading a new page with unknown auth format,
    # We need a try/except block next, so we sleep, rather than using WebDriverWait.until()
    time.sleep(LONG_POLE_WAIT)

    try:
        driver.find_element(By.LINK_TEXT, "Not your account?").click()
    except Exception:
        pass

    driver.find_element(By.ID, "1-email").send_keys(login_email)
    driver.find_element(By.NAME, "password").click()
    driver.find_element(By.NAME, "password").send_keys(login_password)
    driver.find_element(By.CSS_SELECTOR, ".auth0-label-submit").click()
    return driver


def search_for_date(driver, booking_datetime):
    # Set month in calendar search widget
    dropdown = WebDriverWait(driver, DRIVER_WAIT).until(
        ec.presence_of_element_located((By.XPATH, "//select[@class='xpf']")))

    dropdown.find_element(
        By.XPATH,
        "//option[. = '{}']".format(booking_datetime.strftime("%B"))).click()

    # Set year in calendar search widget
    year_select = driver.find_element(By.XPATH, "//input[@class='xjv']")
    year_select.clear()
    year_select.send_keys(booking_datetime.year)

    # The dates only update after click outside of the year input box
    # If we click "enter", it activates search (prematurely), so we click on a random div
    driver.find_element(
        By.XPATH, "//span[text()='San Francisco Recreation & Parks']").click()

    # Set day in calendar search widget
    # class xod is for days in the next month, xof is the previous month, xoe is current month
    try:
        driver.find_element(
            By.XPATH, "//td[@class='xoe' and text()='{}']".format(
                booking_datetime.day)).click()
    # if the desired date is tomorrow, then it is class xo2
    except Exception:
        driver.find_element(
            By.XPATH, "//td[@class='xo2 p_AFSelected' and text()='{}']".format(
                booking_datetime.day)).click()

    # Advance to search page
    time.sleep(LONG_POLE_WAIT)
    driver.find_element(By.LINK_TEXT, "search").click()


def is_next_page(driver):
    # if we are on the last page, then the img will have src='/img/icon/next_disabled.png'
    return len(
        driver.find_elements(By.XPATH,
                             "//img[@src='/img/icon/next.png']")) == 1


def check_next_page(driver, court_location):
    print('checking the next page')
    driver.find_element(By.XPATH, "//img[@src='/img/icon/next.png']").click()
    time.sleep(LONG_POLE_WAIT)
    return identify_relevant_courts(driver, court_location)


def identify_relevant_courts(driver, court_location):
    # Wait for the divs with the courts to load, class xt7
    WebDriverWait(driver, DRIVER_WAIT).until(
        ec.presence_of_element_located((By.CSS_SELECTOR, ".xt7")))

    # return driver.find_elements(By.XPATH, "//span[contains(text(),'{}')]".format(court_location))

    relevant_courts = driver.find_elements(
        By.XPATH, "//span[contains(text(),'{}')]".format(court_location))

    if len(relevant_courts) > 0:
        return relevant_courts
    elif is_next_page(driver):
        return check_next_page(driver, court_location)
    else:
        raise ValueError(
            'Could not find court location: {}'.format(court_location))


def find_booking_link(driver, court_link, court_location, booking_datetime):
    court_name = court_link.text

    court_div = court_link.find_element_by_xpath('..').find_element_by_xpath(
        '..').find_element_by_xpath('..').find_element_by_xpath(
            '..').find_element_by_xpath('..')

    booking_links = court_div.find_elements_by_link_text('{}'.format(
        booking_datetime.strftime("%I:%M %p")))

    if len(booking_links) == 0:
        raise ValueError('{} is not a valid booking time for {}'.format(
            booking_datetime.strftime("%I:%M %p"), court_location))
    else:
        return booking_links[0], court_name


def check_booking_availability(driver, booking_link):
    return len(
        booking_link.find_element_by_xpath('..').find_element_by_xpath(
            '..').find_element_by_xpath('..').find_elements_by_xpath(
                ".//span[text()='Booked']")) == 0


def check_reached_use_booking_limit(driver, booking_datetime):
    time.sleep(LONG_POLE_WAIT)
    user_reached_limits_modal = driver.find_elements(
        By.XPATH,
        "//div[text()='You have reached the limit of bookings allowed on this Spot']"
    )
    if len(user_reached_limits_modal) == 1:
        raise ValueError('User already has a booking on {}'.format(
            booking_datetime.strftime("%a, %b %d, %Y")))
    return


def make_booking(driver, booking_link, booking_datetime, booking_id, username):
    booking_link.click()

    check_reached_use_booking_limit(driver, booking_datetime)

    # Some of the "Book Now" buttons are blocked by the "Support button"
    # So we remove this overlaid element with some embedded JavaScript
    # The overlay is present from the original page load, so we don't have to wait for it
    overlay = WebDriverWait(driver, DRIVER_WAIT).until(
        ec.presence_of_element_located((
            By.XPATH,
            "//iframe[@title='Opens a widget where you can find more information']"
        )))
    driver.execute_script(
        """
        var element = arguments[0];
        element.parentNode.removeChild(element);
    """, overlay)

    WebDriverWait(driver, DRIVER_WAIT).until(
        ec.presence_of_element_located((By.LINK_TEXT, 'Book Now'))).click()

    # Confirmation page loads
    confirmation_span = WebDriverWait(driver, DRIVER_WAIT).until(
        ec.presence_of_element_located(
            (By.XPATH, "//span[contains(text(),'Reservation # ')]")))

    # Note: we refer to this as the booking number, to be consistent with the general
    # use of "booking" in this codebase, but Spotery calls this a reservation number
    booking_number = confirmation_span.text.split('#')[1].strip(' ')

    screenshot_path = 'media/booking_id_{}.png'.format(booking_id)
    driver.get_screenshot_as_file(screenshot_path)
    return booking_number, screenshot_path


def confirm_unsuccessful_booking(booking_datetime, court_location):
    return 'No available courts at {} on {}'.format(
        court_location, booking_datetime.strftime("%a, %b %d, %Y at %I:%M %p"))


def book_court(driver, root_url, login_email, login_password, booking_datetime,
               court_location, booking_id, username):
    check_desired_date(booking_datetime)
    driver = authenticate(driver, root_url, login_email, login_password)
    search_for_date(driver, booking_datetime)
    court_links = identify_relevant_courts(driver, court_location)

    booking_successful = False

    for court_link in court_links:
        booking_link, court_name = find_booking_link(driver, court_link,
                                                     court_location,
                                                     booking_datetime)

        if booking_link is None:
            continue

        booking_available_indicator = check_booking_availability(
            driver, booking_link)

        if booking_available_indicator:
            booking_number, screenshot_path = make_booking(
                driver, booking_link, booking_datetime, booking_id, username)
            return True, booking_number, screenshot_path, court_name, None
            break

    if not booking_successful:
        return False, None, None, None, confirm_unsuccessful_booking(
            booking_datetime, court_location)


class Command(BaseCommand):
    help = """python manage.py execute_pending_bookings
        Attempts to book all bookings with status=pending.
        """

    def handle(self, *args, **options):
        pending_bookings = Booking.objects.filter(status='Pending')
        pending_booking_count = len(pending_bookings)

        for i, booking in enumerate(pending_bookings):
            print('Working on booking {} of {} for {}: {} at {}'.format(
                i + 1, pending_booking_count, booking.user,
                booking.court_location,
                booking.datetime.astimezone(pacific).strftime(
                    '%a, %b %d, %Y %I:%M %p %Z')))

            if os.environ['ENVIRONMENT'] == 'local':
                # Use for local dev
                CHROMEDRIVER_PATH = 'WebDriver/bin/chromedriver'
                driver = webdriver.Chrome(CHROMEDRIVER_PATH)
            else:
                # Use for Heroku
                CHROMEDRIVER_PATH = "/app/.chromedriver/bin/chromedriver"
                chrome_bin = os.environ.get('GOOGLE_CHROME_SHIM', None)
                options = webdriver.ChromeOptions()
                options.binary_location = chrome_bin
                options.add_argument("--disable-gpu")
                options.add_argument("--no-sandbox")
                options.add_argument('headless')
                options.add_argument('window-size=1200x600')
                driver = webdriver.Chrome(CHROMEDRIVER_PATH,
                                          chrome_options=options)

            try:
                booking_successful, booking_number, screenshot_path, court_name, failure_reason = book_court(
                    driver, ROOT_URL, booking.user.user_profile.spotery_login,
                    booking.user.user_profile.spotery_password,
                    booking.datetime.astimezone(pacific),
                    booking.court_location, booking.id, booking.user.username)
            except ValueError as _failure_reason:
                booking_successful = False
                failure_reason = _failure_reason
            # sometimes book_court() will fail without a ValueError
            # else:
            #     booking.status = 'Failed'
            #     booking.failure_reason = 'Not a ValueError'
            #     booking.save()
            #     continue

            if booking_successful:
                booking.status = 'Succeeded'
                booking.booking_number = booking_number
                booking.confirmation_screenshot_path = screenshot_path
                # TODO: align naming court_number = court_name
                booking.court_number = court_name

                email = EmailMessage(
                    'Tennis Court Booking Successful',
                    '''\
                    <html>
                      <head></head>
                      <body>
                        <p>We booked {} for you on {}.</p>
                        <p>Your booking confirmation is attached; please bring this with you to the court.</p>
                        <p>If you need to cancel or change your booking, please do so at <a href="https://spotery.com/f/adf.task-flow?adf.tfDoc=%2FWEB-INF%2Ftaskflows%2Ffacility%2Ftf-faci-detail.xml&psOrgaAlias=sfrp&adf.tfId=tf-faci-detail">SF Tennis Court Reservations</a>, using:</p>
                        <ul>
                            <li>login: {}</li>
                            <li>password: {}</li>
                        </ul>
                        <p>Please be a good SF tennis citizen and cancel your booking if you aren't able to use it!</p>
                        <p>Happy Hitting,</p>
                        <p>Booking Bot</p>
                      </body>
                    </html>
                    '''.format(
                        court_name,
                        booking.datetime.astimezone(pacific).strftime(
                            '%a, %b %d, %Y %I:%M %p %Z'),
                        booking.user.user_profile.spotery_login,
                        booking.user.user_profile.spotery_password),
                    EMAIL_HOST_USER,
                    [booking.user.email],
                )
                email.attach_file(screenshot_path)
                email.content_subtype = "html"
                email.send()

            else:
                booking.status = 'Failed'
                booking.failure_reason = failure_reason
                booking.save()
                email = EmailMessage(
                    'Tennis Court Booking Failed',
                    '''\
                    <html>
                      <head></head>
                      <body>
                        <p>Oh no! We tried and failed to book a court for you at {} on {}. The reason was: {}</p>
                      </body>
                    </html>
                    '''.format(
                        booking.court_location,
                        booking.datetime.astimezone(pacific).strftime(
                            '%a, %b %d, %Y %I:%M %p %Z'), failure_reason),
                    EMAIL_HOST_USER,
                    [booking.user.email],
                )
                email.content_subtype = "html"
                email.send()

            booking.save()
            driver.quit()
