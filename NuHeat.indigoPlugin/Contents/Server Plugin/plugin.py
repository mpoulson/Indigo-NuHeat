#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################

import indigo
import os
import sys
import random
import re
import urllib2
import urllib
import time
from NuHeat import NuHeat
from copy import deepcopy
from ghpu import GitHubPluginUpdater
# Need json support; Use "simplejson" for Indigo support
try:
	import simplejson as json
except:
	import json

# Note the "indigo" module is automatically imported and made available inside
# our global name space by the host process.

################################################################################
kHvacModeEnumToStrMap = {
	indigo.kHvacMode.Cool				: u"cool",
	indigo.kHvacMode.Heat				: u"heat",
	indigo.kHvacMode.HeatCool			: u"auto", # Not Supported
	indigo.kHvacMode.Off				: u"off",
	indigo.kHvacMode.ProgramHeat		: u"program heat", #Not supported
	indigo.kHvacMode.ProgramCool		: u"program cool", #Not supported
	indigo.kHvacMode.ProgramHeatCool	: u"program auto" # Not supported
}

kFanModeEnumToStrMap = {
	indigo.kFanMode.AlwaysOn			: u"always on",
	indigo.kFanMode.Auto				: u"auto"
}

map_to_indigo_hvac_mode={'cool':indigo.kHvacMode.Cool,
						'heat':indigo.kHvacMode.Heat,
						'auto':indigo.kHvacMode.HeatCool,
						'range':indigo.kHvacMode.HeatCool,
						'off':indigo.kHvacMode.Off}

def _lookupActionStrFromHvacMode(hvacMode):
	return kHvacModeEnumToStrMap.get(hvacMode, u"unknown")

def _lookupActionStrFromFanMode(fanMode):
	return kFanModeEnumToStrMap.get(fanMode, u"unknown")

################################################################################
class Plugin(indigo.PluginBase):
	########################################
	def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
		super(Plugin, self).__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
		self.NuHeat = NuHeat(self)
		self.debug = pluginPrefs.get("debug", False)
		self.UserID = None
		self.Password = None
		self.deviceList = {}
		self.loginFailed = False

	######################
	def _changeTempSensorValue(self, dev, index, value, keyValueList):
		# Update the temperature value at index. If index is greater than the "NumTemperatureInputs"
		# an error will be displayed in the Event Log "temperature index out-of-range"
		stateKey = u"temperatureInput" + str(index)
		keyValueList.append({'key':stateKey, 'value':value, 'uiValue':"%d °F" % (value)})
		self.debugLog(u"\"%s\" updating %s %d" % (dev.name, stateKey, value))

	######################
	# Poll all of the states from the thermostat and pass new values to
	# Indigo Server.
	def _refreshStatesFromHardware(self, dev, logRefresh, commJustStarted):

		thermostatId = dev.pluginProps["thermostatId"]
		self.debugLog(u"Getting data for thermostatId: %s" % thermostatId)

		thermostat = NuHeat.GetThermostat(self.NuHeat,thermostatId)

		self.debugLog(u"Device Name: %s" % thermostat.room)
		self.debugLog(u"***Device SystemSwitch: %s" % thermostat.Mode)

		try: self.updateStateOnServer(dev, "name", thermostat.room)
		except: self.de (dev, "name")
		try: self.updateStateOnServer(dev, "setpointHeat", float(thermostat.SetPointTemp))
		except: self.de (dev, "setpointHeat")
	
		try: self.updateStateOnServer(dev, "maxHeatSetpoint", thermostat.MaxTemp)
		except: self.de (dev, "maxHeatSetpoint")
		try: self.updateStateOnServer(dev, "minHeatSetpoint", thermostat.MinTemp)
		except: self.de (dev, "minHeatSetpoint")
		try: self.updateStateOnServer(dev, "online", thermostat.Online)
		except: pass
		try: self.updateStateOnServer(dev, "temperatureInput1", thermostat.Temperature)
		except: pass

		self.debugLog(u"Heating: %s" % thermostat.Heating)
		if  thermostat.Heating == True:
			self.updateStateOnServer(dev, "hvacHeaterIsOn", True)
		else:
			self.updateStateOnServer(dev, "hvacHeaterIsOn", False)

	def updateStateOnServer(self, dev, state, value):
		if dev.states[state] != value:
			self.debugLog(u"Updating Device: %s, State: %s, Value: %s" % (dev.name, state, value))
			dev.updateStateOnServer(state, value)

	def de (self, dev, value):
		self.errorLog ("[%s] No value found for device: %s, field: %s" % (time.asctime(), dev.name, value))

	######################
	# Process action request from Indigo Server to change a cool/heat setpoint.
	def _handleChangeSetpointAction(self, dev, newSetpoint, Permanent, duration, logActionName, stateKey):
		self.debugLog(u"_handleChangeSetpointAction - StateKey: %s" % stateKey)

		sendSuccess = False
		thermostatId = dev.pluginProps["thermostatId"]
		self.debugLog(u"Getting data for thermostatId: %s" % thermostatId)
		self.debugLog(u"NewSetpoint: %s, Permanent: %s, Duration: %s" % (newSetpoint, Permanent, duration))

		thermostat = NuHeat.GetThermostat(self.NuHeat,thermostatId)

		if stateKey == u"setpointHeat":
			# if newSetpoint < dev.states["maxHeatSetpoint"]:
			# 	newSetpoint = dev.states["maxHeatSetpoint"]
			# elif newSetpoint > dev.states["minHeatSetpoint"]:
			# 	newSetpoint = dev.states["minHeatSetpoint"]
			if Permanent == True:
				duration = -1
			sendSuccess = self.NuHeat.SetThermostatHeatSetpoint(thermostat,newSetpoint, duration)
			
		if sendSuccess:
			# If success then log that the command was successfully sent.
			indigo.server.log(u"sent \"%s\" %s to %.1f°" % (dev.name, logActionName, float(newSetpoint)))

			# And then tell the Indigo Server to update the state.
			if stateKey in dev.states:
				dev.updateStateOnServer(stateKey, newSetpoint, uiValue="%.1f °F" % float(newSetpoint))
		else:
			# Else log failure but do NOT update state on Indigo Server.
			indigo.server.log(u"send \"%s\" %s to %.1f° failed" % (dev.name, logActionName, float(newSetpoint)), isError=True)
	def _handleResumeScheduleAction(self, dev, logActionName, stateKey):
		self.debugLog(u"_handleResumeScheduleAction - StateKey: %s" % stateKey)

		sendSuccess = False
		thermostatId = dev.pluginProps["thermostatId"]
		self.debugLog(u"Getting data for thermostatId: %s" % thermostatId)
		
		thermostat = NuHeat.GetThermostat(self.NuHeat,thermostatId)

		sendSuccess = self.NuHeat.SetResumeSchedule(thermostat)
			
		if sendSuccess:
			# If success then log that the command was successfully sent.
			indigo.server.log(u"sent \"%s\" %s" % (dev.name, logActionName))

		else:
			# Else log failure but do NOT update state on Indigo Server.
			indigo.server.log(u"send \"%s\" %s failed" % (dev.name, logActionName), isError=True)

	########################################
	def startup(self):
		self.debugLog(u"NuHeat startup called")
		self.debug = self.pluginPrefs.get('showDebugInLog', False)

		self.updater = GitHubPluginUpdater(self)
		self.updater.checkForUpdate()
		self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', 24)) * 60.0 * 60.0
		self.debugLog(u"updateFrequency = " + str(self.updateFrequency))
		self.next_update_check = time.time()
		self.login(False)

	def login(self, force):
		if self.NuHeat.startup(force) == False:
			indigo.server.log(u"Login to mynewheat.com site failed.  Canceling processing!", isError=True)
			self.loginFailed = True
			return
		else:
			self.loginFailed = False

		self.buildAvailableDeviceList()

	def shutdown(self):
		self.debugLog(u"shutdown called")

	########################################
	def runConcurrentThread(self):
		try:
			while True:
				if self.loginFailed == False:
					if (self.updateFrequency > 0.0) and (time.time() > self.next_update_check):
						self.next_update_check = time.time() + self.updateFrequency
						self.updater.checkForUpdate()

					for dev in indigo.devices.iter("self"):
						if not dev.enabled:
							continue

						# Plugins that need to poll out the status from the thermostat
						# could do so here, then broadcast back the new values to the
						# Indigo Server.
						#self._refreshStatesFromHardware(dev, False, False)

				self.sleep(20)
		except self.StopThread:
			pass	# Optionally catch the StopThread exception and do any needed cleanup.

	########################################
	def validateDeviceConfigUi(self, valuesDict, typeId, devId):
		indigo.server.log(u"validateDeviceConfigUi \"%s\"" % (valuesDict))
		return (True, valuesDict)

	def validatePrefsConfigUi(self, valuesDict):
		self.debugLog(u"Vaidating Plugin Configuration")
		errorsDict = indigo.Dict()
		if len(errorsDict) > 0:
			self.errorLog(u"\t Validation Errors")
			return (False, valuesDict, errorsDict)
		else:
			self.debugLog(u"\t Validation Succesful")
			return (True, valuesDict)
		return (True, valuesDict)

	########################################
	def deviceStartComm(self, dev):
		if self.loginFailed == True:
			return

		# Called when communication with the hardware should be established.
		# Here would be a good place to poll out the current states from the
		# thermostat. If periodic polling of the thermostat is needed (that
		# is, it doesn't broadcast changes back to the plugin somehow), then
		# consider adding that to runConcurrentThread() above.
		self.initDevice(dev)

		dev.stateListOrDisplayStateIdChanged()
		#self._refreshStatesFromHardware(dev, True, True)

	def deviceStopComm(self, dev):
		# Called when communication with the hardware should be shutdown.
		pass

	########################################
	# Thermostat Action callback
	######################
	# Main thermostat action bottleneck called by Indigo Server.
	#Called when the device is changed via UI
	def actionControlThermostat(self, action, dev):
		###### SET HVAC MODE ######
		if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
			self._handleChangeHvacModeAction(dev, action.actionMode)

		###### SET FAN MODE ######
		elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
			self._handleChangeFanModeAction(dev, action.actionMode)

		###### SET HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
			newSetpoint = action.actionValue
			self._handleChangeSetpointAction(dev, newSetpoint, u"change heat setpoint", u"setpointHeat")

		###### DECREASE/INCREASE HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint - action.actionValue
			self._handleChangeSetpointAction(dev, newSetpoint, u"decrease heat setpoint", u"setpointHeat")

		elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint + action.actionValue
			self._handleChangeSetpointAction(dev, newSetpoint, u"increase heat setpoint", u"setpointHeat")

		###### REQUEST STATE UPDATES ######
		elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
		indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
		indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
			self._refreshStatesFromHardware(dev, True, False)

	########################################
	# Actions defined in MenuItems.xml. In this case we just use these menu actions to
	# simulate different thermostat configurations (how many temperature and humidity
	# sensors they have).
	####################
	def _actionSetMode(self, pluginAction):
		self.debugLog(u"\t Setting Mode: %s" % pluginAction.props.get("mode"))
		dev = indigo.devices[pluginAction.deviceId]
		self._handleChangeHvacModeAction(dev,map_to_indigo_hvac_mode[pluginAction.props.get("mode")])

	def _actionSetpoint(self, pluginAction):
		self.debugLog(u"\t Set %s - Setpoint: %s" % (pluginAction.pluginTypeId, pluginAction.props.get("Temprature")))
		dev = indigo.devices[pluginAction.deviceId]

		self.debugLog(u"\t Permanent: %s " % (pluginAction.props))
		self._handleChangeSetpointAction(dev, pluginAction.props.get("Temprature"), pluginAction.props.get("Timing"), pluginAction.props.get("Duration"), "Action Setpoint",pluginAction.pluginTypeId)
	def _actionResumeSchedule(self, pluginAction):
		self.debugLog(u"\t Resume Schedule")
		dev = indigo.devices[pluginAction.deviceId]

		self._handleResumeScheduleAction(dev, "Action Resume Schedule",pluginAction.pluginTypeId)
	def closedPrefsConfigUi(self, valuesDict, userCancelled):
		if not userCancelled:
			#Check if Debugging is set
			try:
				self.debug = self.pluginPrefs[u'showDebugInLog']
			except:
				self.debug = False

			try:
				if (self.UserID != self.pluginPrefs["UserID"]) or \
					(self.Password != self.pluginPrefs["Password"]):
					indigo.server.log("[%s] Replacting Username/Password." % time.asctime())
					self.UserID = self.pluginPrefs["UserID"]
					self.Password = self.pluginPrefs["Password"]
			except:
				pass

			indigo.server.log("[%s] Processed plugin preferences." % time.asctime())
			self.login(True)
			return True
	def validateDeviceConfigUi(self, valuesDict, typeId, devId):
		self.debugLog(u"validateDeviceConfigUi called with valuesDict: %s" % str(valuesDict))
		# Set the address
		valuesDict["ShowCoolHeatEquipmentStateUI"] = True
		return (True, valuesDict)

	def initDevice(self, dev):
		new_props = dev.pluginProps
		new_props['SupportsHvacFanMode'] = False
		new_props['SupportsHvacOperationMode'] = False
		dev.replacePluginPropsOnServer(new_props)

		self.debugLog("Initializing thermostat device: %s" % dev.name)

	def buildAvailableDeviceList(self):
		self.debugLog("Building Available Device List")

		self.deviceList = self.NuHeat.GetDevices()

		indigo.server.log("Number of thermostats found: %i" % (len(self.deviceList)))
		for (k, v) in self.deviceList.iteritems():
			indigo.server.log("\t%s (id: %s)" % (v.room, k))

	def showAvailableThermostats(self):
		indigo.server.log("Number of thermostats found: %i" % (len(self.deviceList)))
		for (id, details) in self.deviceList.iteritems():
			indigo.server.log("\t%s (id: %s)" % (details.room, id))

	def thermostatList(self, filter, valuesDict, typeId, targetId):
		self.debugLog("thermostatList called")
		deviceArray = []
		deviceListCopy = deepcopy(self.deviceList)
		for existingDevice in indigo.devices.iter("self"):
			for id in self.deviceList:
				self.debugLog("    comparing %s against deviceList item %s" % (existingDevice.pluginProps["thermostatId"],id))
				if existingDevice.pluginProps["thermostatId"] == id:
					self.debugLog("    removing item %s" % (id))
					del deviceListCopy[id]
					break

		if len(deviceListCopy) > 0:
			for (id,value) in deviceListCopy.iteritems():
				deviceArray.append((id,value.room))
		else:
			if len(self.deviceList):
				indigo.server.log("All thermostats found are already defined")
			else:
				indigo.server.log("No thermostats were discovered on the network - select \"Rescan for Thermostats\" from the plugin's menu to rescan")

		self.debugLog("    thermostatList deviceArray:\n%s" % (str(deviceArray)))
		return deviceArray

	def thermostatSelectionChanged(self, valuesDict, typeId, devId):
		self.debugLog("thermostatSelectionChanged")
		if valuesDict["thermostat"] in self.deviceList:
			selectedThermostatData = self.deviceList[valuesDict["thermostat"]]
			valuesDict["address"] = valuesDict["thermostat"]
			valuesDict["thermostatId"] = valuesDict["thermostat"]
			valuesDict["name"] = selectedThermostatData.room
		self.debugLog("    thermostatSelectionChanged valuesDict to be returned:\n%s" % (str(valuesDict)))
		return valuesDict
	##########################################
	def checkForUpdates(self):
		self.updater.checkForUpdate()

	def updatePlugin(self):
		self.updater.update()

	def forceUpdate(self):
		self.updater.update(currentVersion='0.0.0')
