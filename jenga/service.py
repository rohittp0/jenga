import os
import sys
import airtable
import requests
from flask import request, jsonify
from dotenv import load_dotenv

from jenga import app

## jwt utility tools
from jenga.jwt.encode import jenga_jwt_encoder
from jenga.jwt.decorator import token_required

from jenga.services.msg91 import sendmessage
from jenga.services.airtable import AirTableDB

# error handler
from jenga.error import InvalidUsage
import logging


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s : %(levelname)s : %(name)s : %(message)s"
)

load_dotenv()

"""
    deta and airtable configurations
"""
airtable_db = AirTableDB(
    base_key=app.config.get("AIRTABLE_BASE_KEY"),
    api_key=app.config.get("AIRTABLE_API_KEY"),
)

"""
    Auth Route
"""


@app.route("/user", methods=["GET"])
@token_required
def get_auth_status(user):
    """
    returns user information of status
    number, verified, memberShipID
    """
    user = airtable_db.get_member_details(user["memberShipID"])

    if not user :
        raise InvalidUsage("User not registered", status_code=401)

    return jsonify(user)


"""
    Membership Routes
"""


@app.route("/", methods=["POST"])
def generate():
    """
    inputs a mobile number as JSON key
    generates an OTP
    sends to the given mobile number
    """
    number = request.json["number"]
    logging.info(number)
    if len(number) != 10:
        logging.info("Invalid Phone number")
        raise InvalidUsage("Invalid Phone number", status_code=417)

    # print("session phone number set")
    # db.put({"key":number,"stage":"otp"})
    sendmessage.send_otp(number)
    logging.info("Otp has been generated successfully")
    token = jenga_jwt_encoder(number=number)
    return {
        "message": "Otp has been send. Check your number",
        "token": token.decode("UTF-8"),
    }
    # code=307 does a POST request reference : https://stackoverflow.com/questions/15473626/make-a-post-request-while-redirecting-in-flask
    # except:
    #     e = sys.exc_info()[0]
    #     print("Error :", str(e), e)
    #     return redirect(url_for('generate'))


@app.route("/retry", methods=["POST"])
@token_required
def retry_otp(user):
    """
    to send otp again when its not received
    token is required
    payload : {
        "retry_type":"voice or text"
    }
    """
    type_of_retry = request.json["retry_type"]
    number = user.get("number")

    if not (type_of_retry == "voice" or type_of_retry == "text"):
        logging.info("Invalid retry type")
        raise InvalidUsage("Invalid otp retry type", status_code=417)

    data = sendmessage.retry_otp(mobile=number, type=type_of_retry)
    print(data)
    if data["type"] == "success":
        logging.info("otp retry success")
        return {"success": 200}
    else:
        logging.error(data["message"])
        raise InvalidUsage(data["message"], status_code=417)


@app.route("/validate", methods=["POST"])
@token_required
def validate(user):
    """
    to validate an OTP send via GET: /
    checks validity of the OTP
    on verified checks number exist if it does sends back the membershipID
    else sends the new token
    """
    entered_otp = request.json["otp"]
    logging.info("entered_code : %s", entered_otp)
    if len(entered_otp) != 4:
        logging.info("Invalid OTP")
        raise InvalidUsage("Invalid OTP", status_code=417)

    if user.get("number") is not None:
        phone_number = user["number"]
        status = sendmessage.verify_otp(phone_number, entered_otp)
        # db.update({"stage":"done","MembershipId":"RandomID"},key=phone_number)
        # status = True
        if status is True:
            logging.info("STATUS : %s", status)
            already_exists = airtable_db.check_member_exist(phone_number)
            if already_exists:
                logging.info("member already exists")
                new_token = jenga_jwt_encoder(
                    verified=True, memberShipID=already_exists
                )
                raise InvalidUsage(
                    "user already exist",
                    status_code=419,
                    payload={
                        "memberShipID": already_exists,
                        "token": new_token.decode("UTF-8"),
                    },
                )
            else:
                new_token = jenga_jwt_encoder(number=phone_number, verified=True)
                return {
                    "message": "successfully signed up",
                    "token": new_token.decode("UTF-8"),
                }
        else:
            logging.info("STATUS : %s", status)
            raise InvalidUsage("status failed", status_code=417)
    else:
        raise InvalidUsage("Time expired, retry again", status_code=404)


@app.route("/details", methods=["POST"])
@token_required
def details(user):
    """
    user form registration
    accepts various details of user like college etc
    saves it to deta and airtable
    returns membershipid and token
    """
    number = user.get("number")
    if number is None or user.get("verified") is None:
        raise InvalidUsage("Unauthorized access", status_code=401)

    already_exists = airtable_db.check_member_exist(number)
    if already_exists:
        logging.info("member already exists")
        new_token = jenga_jwt_encoder(memberShipID=already_exists, verified=True)
        raise InvalidUsage(
            "user already exist",
            status_code=419,
            payload={"memberID": already_exists, "payload": new_token},
        )

    data = request.get_json()
    # data["AreasOfInterest"] = request.form.to_dict(flat=False)["AreasOfInterest"]  #removed this question from html
    if data.get("College"):
        data["College"] = [data["College"]]

    data["MobileNumber"] = int(number)
    if data.get("My_Skills"):
        data["My_Skills"] = data.get("My_Skills").strip().split(",")

    logging.info(data)
    try:
        record = airtable_db.insert_member_details(data)
        logging.info(record)
        # db.put({"key": number, "MembershipId": record["id"]})
        new_token = jenga_jwt_encoder(memberShipID=record["id"], verified=True)
        return {
            "message": "Successfully registered",
            "memberShipID": record["id"],
            "token": new_token.decode("UTF-8"),
        }
    except requests.HTTPError as exception:
        e = sys.exc_info()[0]
        logging.info("Error : %s", str(e))
        print(exception)
        raise InvalidUsage(str(e), status_code=417)


@app.route("/edit", methods=["POST"])
@token_required
def edit_details(user,details):
    """

    User details updating
    accepts various details of user like college etc
    saves it to airtable
    returns status
    """
    number = user.get("number")
    if number is None or user.get("verified") is None:
        raise InvalidUsage("Unauthorized access", status_code=401)

    id = airtable_db.check_member_exist(number)
    if not id:
        raise InvalidUsage("user dosenot exist", status_code=419)    

    data = request.get_json()

    if data.get("College"):
        data["College"] = [data["College"]]

    data["MobileNumber"] = int(number)
    if data.get("My_Skills"):
        data["My_Skills"] = data.get("My_Skills").strip().split(",")

    try:
        record = airtable_db.update_member_details(id,data)
        logging.info(record)
        return {
            "message": "Successfully edited"
        }
    except requests.HTTPError as exception:
        e = sys.exc_info()[0]
        logging.info("Error : %s", str(e))
        print(exception)
        raise InvalidUsage(str(e), status_code=417)

"""
    Utility Routes
"""


@app.route("/colleges", methods=["GET"])
def get_college_list():
    """
    get all colleges saved in DB from airtable
    """
    college_list = airtable_db.get_colleges()
    return jsonify(college_list)


@app.route("/skills", methods=["GET"])
def get_skills_list():
    """
    get all skills saved in DB from airtable
    """
    skill_list = airtable_db.get_skills()
    return jsonify(skill_list)


"""
    Error Handler Routes
"""


@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    """
    error handler to fire all errors from flask
    """
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response
